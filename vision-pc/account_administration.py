"""Owner-only, irreversible removal of Firebase identities and their local Vision data.

The Firebase Admin service-account JSON stays on the PC, outside the repository.
A persistent tombstone is written *before* the remote Auth deletion so already
issued ID tokens cannot recreate local data if a cleanup needs to be retried.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time

from flask import g, request

PUBLIC_ID = re.compile(r"^[0-9a-f]{64}$")
LOCAL_ID = re.compile(r"^[A-Za-z0-9_-]{1,200}$")
VORTEX_ID = re.compile(r"^[0-9a-f]{24}$")
ACTIVE = ("queued", "preparing", "processing", "submitting", "in_progress",
          "saving", "started", "pending", "uploading", "downloading", "transcribing")
_LOCK = threading.RLock()


class RemovalError(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def _tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _count(db, tables, table, where, args):
    return db.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", args).fetchone()[0] if table in tables else 0


def _delete(db, tables, table, where, args):
    if table in tables:
        db.execute(f"DELETE FROM {table} WHERE {where}", args)


def _target(db, public_id):
    if not isinstance(public_id, str) or not PUBLIC_ID.fullmatch(public_id):
        raise RemovalError("Choose a valid account.", 400)
    for row in db.execute("SELECT uid,name,email FROM audit_users"):
        if hashlib.sha256(row["uid"].encode()).hexdigest() == public_id:
            return dict(row)
    raise RemovalError("This account is no longer in the directory.", 404)


def _admins(config):
    path = Path(config["DATA_DIR"]) / "activity-admins.json"
    if path.is_symlink() or path.stat().st_size > 65536:
        raise RemovalError("The administrator list is unavailable.", 503)
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    uids = value.get("uids") if isinstance(value, dict) else None
    if not isinstance(uids, list) or not uids:
        raise RemovalError("The administrator list is unavailable.", 503)
    return set(uids)


def _scope(db, uid):
    tables = _tables(db)
    projects = {r[0] for r in db.execute("SELECT id FROM projects WHERE owner_uid=?", (uid,))} if "projects" in tables else set()
    conversations = {r[0] for r in db.execute("SELECT id FROM venture_conversations WHERE uid=?", (uid,))} if "venture_conversations" in tables else set()
    ids = projects | conversations
    if any(not LOCAL_ID.fullmatch(pid) for pid in ids):
        raise RemovalError("An account project has an invalid storage identifier. Resolve this on the PC.", 409)
    if "projects" in tables:
        for cid in conversations - projects:
            conflict = db.execute("SELECT owner_uid FROM projects WHERE id=?", (cid,)).fetchone()
            if conflict and conflict[0] not in (None, uid):
                raise RemovalError("A conversation has a different project owner. Resolve this on the PC.", 409)
    vortex = [r[0] for r in db.execute("SELECT id FROM vortex_jobs WHERE uid=?", (uid,))] if "vortex_jobs" in tables else []
    if any(not VORTEX_ID.fullmatch(job) for job in vortex):
        raise RemovalError("A Vortex item has an invalid storage identifier. Resolve this on the PC.", 409)
    return tables, sorted(ids), vortex


def _busy(db, tables, uid, projects, context=None):
    placeholders = ",".join("?" for _ in ACTIVE)
    for table in ("jobs", "vortex_jobs", "venture_dictation_requests"):
        if _count(db, tables, table, f"uid=? AND status IN ({placeholders})", (uid, *ACTIVE)):
            return True
    for table in ("gemini_usage",):
        if _count(db, tables, table, "uid=? AND status='started' AND finished_at IS NULL", (uid,)):
            return True
    for pid in projects:
        for table in ("project_runs", "local_transcriptions", "uploaded_media",
                      "document_jobs"):
            if _count(db, tables, table, f"project_id=? AND status IN ({placeholders})", (pid, *ACTIVE)):
                return True
    if context:
        with context.db() as other:
            ct = _tables(other)
            if "context_jobs" in ct and _count(other, ct, "context_jobs",
                                                "owner=? AND status IN ('queued','processing')", (uid,)):
                return True
    return False


def _preview(app, db, actor, public_id):
    target = _target(db, public_id)
    uid = target["uid"]
    if uid == actor or uid in _admins(app.config):
        raise RemovalError("Administrator accounts cannot be deleted here.", 403)
    tables, projects, vortex = _scope(db, uid)
    context = getattr(app.config.get("SESSIONS_CONTEXT"), "engine", None)
    is_busy = _busy(db, tables, uid, projects, context)
    configured = bool(app.config.get("FIREBASE_ADMIN_CREDENTIALS"))
    return target, projects, vortex, dict(
        id=public_id, name=target["name"], email=target["email"],
        counts=dict(projects=len(projects),
                    ventureConversations=_count(db, tables, "venture_conversations", "uid=?", (uid,)),
                    vortexJobs=len(vortex),
                    visionImports=_count(db, tables, "jobs", "uid=?", (uid,))),
        hasActiveWork=is_busy, adminConfigured=configured,
        canDelete=not is_busy and configured)


def _firebase_client(app):
    path = app.config.get("FIREBASE_ADMIN_CREDENTIALS")
    identity = app.config.get("FIREBASE_IDENTITY")
    if not path or not identity:
        raise RemovalError("Configure Firebase account-deletion credentials on the Vision PC first.", 503)
    credential_path = Path(path)
    if not credential_path.is_absolute() or not credential_path.is_file():
        raise RemovalError("Firebase account-deletion credentials are missing on the Vision PC.", 503)
    try:
        import firebase_admin
        from firebase_admin import auth, credentials
        credential = credentials.Certificate(str(credential_path))
        if credential.project_id != identity.project_id:
            raise RemovalError("The Firebase administrator credentials belong to a different project.", 503)
        try:
            sdk_app = firebase_admin.get_app("vision-account-removal")
        except ValueError:
            sdk_app = firebase_admin.initialize_app(credential, {"projectId": identity.project_id},
                                                    name="vision-account-removal")
        return auth, sdk_app
    except (OSError, ValueError, ImportError, KeyError) as error:
        if isinstance(error, RemovalError):
            raise
        raise RemovalError("Firebase account removal is not configured correctly on the Vision PC.", 503) from None


def _remove_tree(path):
    # Paths are built exclusively from server-controlled roots and validated IDs.
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _remove_local_files(app, uid, projects, vortex, result_paths):
    root = Path(app.config["DATA_DIR"])
    user_dir = root / "users" / hashlib.sha256(uid.encode()).hexdigest()
    _remove_tree(user_dir)
    for pid in projects:
        _remove_tree(root / "projects" / pid)
        context_root = root / "context" / "sources" / hashlib.sha256((uid + "\0" + pid).encode()).hexdigest()
        _remove_tree(context_root)
    for job in vortex:
        _remove_tree(root / "vortex" / job)
    # Older import jobs can retain results outside the project directory.
    safe_root = root.resolve()
    for raw in result_paths:
        if not raw:
            continue
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = root / candidate
        resolved = candidate.resolve()
        if not resolved.is_relative_to(safe_root) or resolved == safe_root:
            raise RemovalError("A saved import points outside Vision's data directory. Review it on the PC.", 409)
        if candidate.is_symlink():
            candidate.unlink()
        elif candidate.is_file():
            candidate.unlink()


def _remove_context(context, uid):
    if context is None:
        return
    with context.db() as db:
        db.execute("BEGIN IMMEDIATE")
        tables = _tables(db)
        if _count(db, tables, "context_jobs", "owner=? AND status='processing'", (uid,)):
            raise RemovalError("Context indexing is still active. Retry after it finishes.", 409)
        for table in ("context_fts", "context_chunks", "context_sources", "context_generations",
                      "context_heads", "context_jobs", "context_cache", "context_vectors"):
            _delete(db, tables, table, "owner=?", (uid,))


def _purge_database(db, uid, projects, tables):
    for pid in projects:
        run_ids = [r[0] for r in db.execute("SELECT id FROM project_runs WHERE project_id=?", (pid,))] if "project_runs" in tables else []
        for rid in run_ids:
            for table in ("run_events", "run_artifacts", "context_responses", "context_uploads",
                          "venture_memory_indexed", "venture_ledger"):
                column = "run_id"
                _delete(db, tables, table, column + "=?", (rid,))
        for table in ("project_runs", "local_transcriptions", "uploaded_media", "uploaded_media_uploads",
                      "document_jobs", "context_sync_jobs"):
            _delete(db, tables, table, "project_id=?", (pid,))
        _delete(db, tables, "venture_memory", "cid=?", (pid,))
        _delete(db, tables, "venture_conversations", "id=? AND uid=?", (pid, uid))
        _delete(db, tables, "projects", "id=? AND owner_uid=?", (pid, uid))
    for table in ("jobs", "responses", "vortex_jobs", "venture_conversations",
                  "venture_preferences", "venture_workspace_preferences", "venture_dictation_requests",
                  "venture_funding", "venture_calibrations", "venture_ledger",
                  "venture_container_costs", "gemini_usage", "gemini_access",
                  "gemini_access_decisions", "account_credentials", "audit_events",
                  "audit_signins", "audit_account_metadata", "audit_user_apps", "audit_users"):
        _delete(db, tables, table, "uid=?", (uid,))


def register(app, connect_db, owner_access, forbidden, respond):
    """Called from the already owner-restricted activity route registration."""
    if "vision_account_remove" in app.view_functions:
        return

    @app.get("/api/admin/accounts/<account_id>")
    def vision_account_preview(account_id):
        if not owner_access():
            return forbidden()
        try:
            with connect_db() as db:
                _, _, _, preview = _preview(app, db, g.uid, account_id)
            return respond(preview)
        except RemovalError as error:
            return respond({"error": str(error)}, error.status)
        except (OSError, sqlite3.Error, ValueError):
            return respond({"error": "Account removal preview is unavailable on the PC."}, 503)

    @app.post("/api/admin/accounts/<account_id>/delete")
    def vision_account_remove(account_id):
        if not owner_access():
            return forbidden()
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {"email", "confirmation"} or body.get("confirmation") != "DELETE":
            return respond({"error": "Type DELETE and the account email to confirm removal."}, 400)
        try:
            with _LOCK:
                with connect_db() as db:
                    target, projects, vortex, preview = _preview(app, db, g.uid, account_id)
                if body.get("email") != target["email"] or not target["email"]:
                    raise RemovalError("The confirmation email does not match the selected account.", 400)
                if preview["hasActiveWork"]:
                    raise RemovalError("This account still has processing work. Stop it and retry.", 409)
                auth, sdk_app = _firebase_client(app)  # Fail before any changes if unconfigured.
                uid = target["uid"]
                with connect_db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    tables, current_projects, current_vortex = _scope(db, uid)
                    if _busy(db, tables, uid, current_projects,
                             getattr(app.config.get("SESSIONS_CONTEXT"), "engine", None)):
                        raise RemovalError("Work started since the preview. Stop it and retry.", 409)
                    db.execute("INSERT OR IGNORE INTO account_removals(uid,created_at) VALUES(?,?)",
                               (uid, time.time()))
                # Delete Firebase Auth before discarding local records. A failed
                # external call leaves the tombstone in place for an admin retry.
                try:
                    auth.delete_user(uid.removeprefix("firebase:"), app=sdk_app)
                except auth.UserNotFoundError:
                    pass
                except Exception:
                    raise RemovalError("Firebase did not confirm account deletion. Retry from this directory.", 503) from None
                with connect_db() as db:
                    tables, projects, vortex = _scope(db, uid)
                    if _busy(db, tables, uid, projects,
                             getattr(app.config.get("SESSIONS_CONTEXT"), "engine", None)):
                        raise RemovalError("A previous processing job is still finishing. Retry removal shortly.", 409)
                    result_paths = [r[0] for r in db.execute("SELECT result_path FROM jobs WHERE uid=?", (uid,))] if "jobs" in tables else []
                _remove_context(getattr(app.config.get("SESSIONS_CONTEXT"), "engine", None), uid)
                _remove_local_files(app, uid, projects, vortex, result_paths)
                with connect_db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    tables, projects, _ = _scope(db, uid)
                    if _busy(db, tables, uid, projects):
                        raise RemovalError("A processing job is still active. Retry when it finishes.", 409)
                    _purge_database(db, uid, projects, tables)
                audit = app.config.get("AUDIT_LOGS")
                if audit:
                    with audit.lock:
                        (audit.directory / audit._filename(uid)).unlink(missing_ok=True)
                        audit.seen = {k: v for k, v in audit.seen.items() if k[0] != uid}
                        audit._index()
                return respond({"deleted": True, "removedProjects": len(projects),
                                "removedVortexJobs": len(vortex)})
        except RemovalError as error:
            return respond({"error": str(error)}, error.status)
        except (OSError, sqlite3.Error, ValueError):
            return respond({"error": "Account cleanup is incomplete. The account is blocked from this PC. Retry removal.", "retryable": True}, 503)
