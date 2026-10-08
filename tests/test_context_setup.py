"""Installer safety checks that use disposable data and never a live processor."""
import importlib.util
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("setup_context", ROOT / "vision-pc/setup_context.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class ContextInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_cold_backup_preserves_sqlite_and_original_files(self):
        source = self.root / "data"
        source.mkdir()
        with closing(sqlite3.connect(source / "venture.sqlite3")) as con, con:
            con.execute("CREATE TABLE projects(id TEXT, payload TEXT)")
            con.execute("INSERT INTO projects VALUES(?,?)", ("owned", "unchanged 0.380 RH"))
        (source / "original.zip").write_bytes(b"opaque-original-bytes")
        (source / "nested").mkdir()
        (source / "nested/notes.txt").write_text("unchanged", encoding="utf-8")
        target = self.root / "backup"
        result = setup.backup_data(source, target)
        self.assertTrue(result["complete"])
        self.assertEqual(result["fileCount"], 3)
        with closing(sqlite3.connect(target / "data/venture.sqlite3")) as con, con:
            self.assertEqual(con.execute("SELECT payload FROM projects").fetchone()[0], "unchanged 0.380 RH")
        self.assertEqual((source / "original.zip").read_bytes(), (target / "data/original.zip").read_bytes())
        manifest = json.loads((target / "backup-manifest.json").read_text())
        for item in manifest["files"]:
            self.assertEqual(item["sha256"], setup._sha256(target / "data" / item["path"]))

    def test_overlapping_or_existing_backup_is_rejected(self):
        source = self.root / "data"
        source.mkdir()
        for destination in (source, source / "nested", self.root):
            with self.assertRaises(ValueError):
                setup.backup_data(source, destination)

    def test_link_cannot_disclose_outside_source(self):
        source = self.root / "data"
        source.mkdir()
        outside = self.root / "outside"
        outside.write_text("private", encoding="utf-8")
        try:
            (source / "link").symlink_to(outside)
        except OSError:
            self.skipTest("Symlinks require additional Windows privileges")
        with self.assertRaises(ValueError):
            setup.backup_data(source, self.root / "backup")
        self.assertFalse((self.root / "backup/backup-manifest.json").exists())

    def test_disk_shortage_and_interrupted_copy_never_mark_complete(self):
        source = self.root / "data"
        source.mkdir()
        (source / "project.json").write_text("original", encoding="utf-8")
        with patch.object(shutil, "disk_usage", return_value=type("Usage", (), {"free": 1})()):
            with self.assertRaises(ValueError):
                setup.backup_data(source, self.root / "low-space")
        with patch.object(shutil, "copy2", side_effect=OSError("simulated interruption")):
            with self.assertRaises(OSError):
                setup.backup_data(source, self.root / "interrupted")
        self.assertFalse((self.root / "interrupted/backup-manifest.json").exists())
        self.assertEqual((source / "project.json").read_text(), "original")

    def test_mutating_source_is_rejected(self):
        source = self.root / "data"
        source.mkdir()
        (source / "project.json").write_text("original", encoding="utf-8")
        original_copy = shutil.copy2
        def changing_copy(src, dest):
            original_copy(src, dest)
            Path(src).write_text("changed during backup", encoding="utf-8")
        with patch.object(shutil, "copy2", side_effect=changing_copy):
            with self.assertRaises(RuntimeError):
                setup.backup_data(source, self.root / "backup")
        self.assertFalse((self.root / "backup/backup-manifest.json").exists())

    def test_model_validation_rejects_custom_code_and_missing_files(self):
        model = self.root / "model"
        model.mkdir()
        with self.assertRaises(ValueError):
            setup.validate_model_files(model)
        for name in setup.MODEL_FILES:
            path = model / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}" if path.suffix == ".json" else "fixture", encoding="utf-8")
        (model / "modules.json").write_text(json.dumps([{"type": "custom_module.Loader", "path": ""}]))
        with self.assertRaisesRegex(ValueError, "custom code"):
            setup.validate_model_files(model)
        (model / "modules.json").write_text(json.dumps([{"type": "sentence_transformers.models.Transformer", "path": "../outside"}]))
        with self.assertRaisesRegex(ValueError, "path"):
            setup.validate_model_files(model)

    def test_hardware_report_observes_environment_without_api_keys(self):
        report = setup.hardware_report(self.root)
        self.assertTrue(report["fts5"])
        self.assertGreater(report["logicalProcessors"], 0)
        self.assertEqual(report["paidApiCalls"], 0)
        self.assertNotIn("token", report)


if __name__ == "__main__":
    unittest.main()
