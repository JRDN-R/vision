"""Regression tests for owner-only account deletion; no real Firebase calls."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from flask import Flask, g, jsonify

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vision-pc"))
import account_administration as accounts


OWNER = "firebase:owner-uid"
TARGET = "firebase:target-uid"
OTHER_ADMIN = "firebase:second-admin"
PROJECT = "project_1234567890"
VORTEX = "a" * 24
TRANSCRIPT = "b" * 24
UPLOAD = "c" * 24
DOCUMENT = "d" * 24
CHUNK_REQUEST = "client-request-1"


class Connection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            return super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.close()


class AccountAdminTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sqlite = self.root / "vision.sqlite3"
        (self.root / "activity-admins.json").write_text(
            json.dumps({"uids": [OWNER, OTHER_ADMIN]}), encoding="utf-8")
        self.uid = TARGET
        self.ident = hashlib.sha256(TARGET.encode()).hexdigest()

        def connect():
            conn = sqlite3.connect(self.sqlite, factory=Connection)
            conn.row_factory = sqlite3.Row
            return conn
        self.connect = connect
        with connect() as db:
            db.executescript("""
                CREATE TABLE audit_users(uid TEXT PRIMARY KEY,name TEXT,email TEXT);
                CREATE TABLE audit_events(uid TEXT,event TEXT);
                CREATE TABLE audit_signins(uid TEXT);
                CREATE TABLE audit_account_metadata(uid TEXT);
                CREATE TABLE audit_user_apps(uid TEXT);
                CREATE TABLE account_removals(uid TEXT PRIMARY KEY,created_at REAL);
                CREATE TABLE projects(id TEXT PRIMARY KEY,owner_uid TEXT);
                CREATE TABLE venture_conversations(id TEXT,uid TEXT);
                CREATE TABLE project_runs(id TEXT,project_id TEXT,status TEXT);
                CREATE TABLE run_events(run_id TEXT);
                CREATE TABLE run_artifacts(run_id TEXT);
                CREATE TABLE venture_memory(run_id TEXT,cid TEXT,uid TEXT);
                CREATE TABLE venture_memory_indexed(run_id TEXT);
                CREATE TABLE account_credentials(uid TEXT);
                CREATE TABLE vortex_jobs(id TEXT,uid TEXT,status TEXT);
                CREATE TABLE jobs(uid TEXT,status TEXT,result_path TEXT);
                CREATE TABLE local_transcriptions(id TEXT,project_id TEXT,status TEXT);
                CREATE TABLE uploaded_media(id TEXT,project_id TEXT,status TEXT);
                CREATE TABLE uploaded_media_uploads(project_id TEXT,request_id TEXT);
                CREATE TABLE document_jobs(id TEXT,project_id TEXT,status TEXT);
            """)
            db.executemany("INSERT INTO audit_users VALUES(?,?,?)", [
                (OWNER, "Vision Owner", "owner@example.test"),
                (OTHER_ADMIN, "Another Owner", "admin@example.test"),
                (TARGET, "Taylor Example", "taylor@example.test")])
            db.execute("INSERT INTO projects VALUES(?,?)", (PROJECT, TARGET))
            db.execute("INSERT INTO venture_conversations VALUES(?,?)", (PROJECT, TARGET))
            db.execute("INSERT INTO project_runs VALUES(?,?,?)", ("run-1", PROJECT, "completed"))
            db.execute("INSERT INTO run_artifacts VALUES(?)", ("run-1",))
            db.execute("INSERT INTO venture_memory VALUES(?,?,?)", ("run-1", PROJECT, TARGET))
            db.execute("INSERT INTO vortex_jobs VALUES(?,?,?)", (VORTEX, TARGET, "complete"))
            db.execute("INSERT INTO account_credentials VALUES(?)", (TARGET,))
            db.execute("INSERT INTO local_transcriptions VALUES(?,?,?)", (TRANSCRIPT, PROJECT, "completed"))
            db.execute("INSERT INTO uploaded_media VALUES(?,?,?)", (UPLOAD, PROJECT, "completed"))
            db.execute("INSERT INTO document_jobs VALUES(?,?,?)", (DOCUMENT, PROJECT, "completed"))
            db.execute("INSERT INTO uploaded_media_uploads VALUES(?,?)", (PROJECT, CHUNK_REQUEST))
        self.directories = [
            self.root / "users" / hashlib.sha256(TARGET.encode()).hexdigest(),
            self.root / "projects" / PROJECT,
            self.root / "vortex" / VORTEX,
            self.root / "transcriptions" / TRANSCRIPT,
            self.root / "uploaded-media" / UPLOAD,
            self.root / "documents" / DOCUMENT,
            self.root / "uploaded-media" / ".uploads" /
            hashlib.sha256((PROJECT + "\0" + CHUNK_REQUEST).encode()).hexdigest(),
        ]
        for directory in self.directories:
            directory.mkdir(parents=True)
            (directory / "private.txt").write_text("private", encoding="utf-8")
        self.app = Flask(__name__)
        self.app.config.update(DATA_DIR=self.root, FIREBASE_IDENTITY=types.SimpleNamespace(project_id="demo-project"),
                               FIREBASE_ADMIN_CREDENTIALS="configured", SESSIONS_CONTEXT=None)
        self.admin_allowed = True

        @self.app.before_request
        def fake_identity():
            g.uid = OWNER
            g.auth_kind = "firebase-google"

        accounts.register(self.app, connect, lambda: self.admin_allowed,
                          lambda: (jsonify(error="Forbidden"), 403),
                          lambda value, status=200: (jsonify(value), status))
        self.client = self.app.test_client()
        self.fake_auth = types.SimpleNamespace(delete_user=lambda uid, app=None: self.deleted.append(uid),
                                               UserNotFoundError=type("NoSuchUser", (Exception,), {}))
        self.deleted = []
        self.firebase = patch.object(accounts, "_firebase_client", return_value=(self.fake_auth, object()))
        self.firebase.start()
        self.addCleanup(self.firebase.stop)

    def preview(self):
        return self.client.get("/api/admin/accounts/" + self.ident)

    def remove(self, email="taylor@example.test", phrase="DELETE"):
        return self.client.post("/api/admin/accounts/" + self.ident + "/delete",
                                json={"email": email, "confirmation": phrase})

    def test_preview_exposes_counts_without_mutation(self):
        response = self.preview()
        self.assertEqual(response.status_code, 200)
        info = response.get_json()
        self.assertTrue(info["canDelete"])
        self.assertEqual(info["counts"]["projects"], 1)
        self.assertEqual(info["counts"]["vortexJobs"], 1)
        self.assertEqual(info["counts"]["ventureConversations"], 1)
        self.assertEqual(self.deleted, [])

    def test_requires_exact_email_and_delete_word(self):
        self.assertEqual(self.remove("wrong@example.test").status_code, 400)
        self.assertEqual(self.remove(phrase="Delete").status_code, 400)
        self.assertTrue(all(d.exists() for d in self.directories))
        with self.connect() as db:
            self.assertIsNone(db.execute("SELECT * FROM account_removals").fetchone())

    def test_deletes_firebase_and_all_account_files_and_records(self):
        response = self.remove()
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.deleted, [TARGET.removeprefix("firebase:")])
        self.assertTrue(all(not d.exists() for d in self.directories))
        with self.connect() as db:
            self.assertIsNone(db.execute("SELECT * FROM audit_users WHERE uid=?", (TARGET,)).fetchone())
            self.assertIsNotNone(db.execute("SELECT * FROM audit_users WHERE uid=?", (OWNER,)).fetchone())
            self.assertIsNone(db.execute("SELECT * FROM projects WHERE owner_uid=?", (TARGET,)).fetchone())
            self.assertIsNone(db.execute("SELECT * FROM vortex_jobs WHERE uid=?", (TARGET,)).fetchone())
            self.assertIsNone(db.execute("SELECT * FROM venture_memory WHERE uid=?", (TARGET,)).fetchone())
            self.assertIsNotNone(db.execute("SELECT * FROM account_removals WHERE uid=?", (TARGET,)).fetchone())

    def test_owner_and_other_admin_are_protected(self):
        for uid in (OWNER, OTHER_ADMIN):
            ident = hashlib.sha256(uid.encode()).hexdigest()
            response = self.client.get("/api/admin/accounts/" + ident)
            self.assertEqual(response.status_code, 403)
        self.assertEqual(self.deleted, [])

    def test_active_jobs_prevent_any_deletion(self):
        with self.connect() as db:
            db.execute("UPDATE vortex_jobs SET status='processing' WHERE uid=?", (TARGET,))
        self.assertFalse(self.preview().get_json()["canDelete"])
        self.assertEqual(self.remove().status_code, 409)
        self.assertEqual(self.deleted, [])

    def test_admin_rights_required_for_read_and_delete(self):
        self.admin_allowed = False
        self.assertEqual(self.preview().status_code, 403)
        self.assertEqual(self.remove().status_code, 403)
        self.assertEqual(self.deleted, [])

    def test_missing_credentials_do_not_touch_account(self):
        self.app.config["FIREBASE_ADMIN_CREDENTIALS"] = ""
        self.assertFalse(self.preview().get_json()["canDelete"])
        self.assertEqual(self.remove().status_code, 503)
        self.assertEqual(self.deleted, [])

    def test_remote_failure_keeps_replay_blocker_and_data(self):
        def fails(uid, app=None):
            raise RuntimeError("simulated Firebase network failure")
        self.fake_auth.delete_user = fails
        self.assertEqual(self.remove().status_code, 503)
        with self.connect() as db:
            self.assertIsNotNone(db.execute("SELECT * FROM audit_users WHERE uid=?", (TARGET,)).fetchone())
            self.assertIsNotNone(db.execute("SELECT * FROM account_removals WHERE uid=?", (TARGET,)).fetchone())
        self.assertTrue(all(d.exists() for d in self.directories))


if __name__ == "__main__":
    unittest.main()
