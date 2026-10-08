"""Fresh scoped server connections release handles after commit and rollback."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import server


class ContextConnectionTests(unittest.TestCase):
    def test_commit_closes_connection_without_waiting_for_gc(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "scope.sqlite3"
            with patch.dict(server.app.config, DATABASE=path):
                with server.connect_db() as db:
                    db.execute("CREATE TABLE fixture(value TEXT)")
                    db.execute("INSERT INTO fixture VALUES('committed')")
                with self.assertRaises(sqlite3.ProgrammingError):
                    db.execute("SELECT * FROM fixture")
                with server.connect_db() as check:
                    self.assertEqual(check.execute("SELECT value FROM fixture").fetchone()[0], "committed")
            # Keep db/check references alive while deleting; Windows must no
            # longer report an open file handle held by a completed transaction.
            path.unlink()

    def test_rollback_closes_connection_and_preserves_prior_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "scope.sqlite3"
            with patch.dict(server.app.config, DATABASE=path):
                with server.connect_db() as initial:
                    initial.execute("CREATE TABLE fixture(value TEXT)")
                    initial.execute("INSERT INTO fixture VALUES('original')")
                with self.assertRaisesRegex(RuntimeError, "stop"):
                    with server.connect_db() as db:
                        db.execute("UPDATE fixture SET value='should roll back'")
                        raise RuntimeError("stop")
                with self.assertRaises(sqlite3.ProgrammingError):
                    db.execute("SELECT * FROM fixture")
                with server.connect_db() as check:
                    self.assertEqual(check.execute("SELECT value FROM fixture").fetchone()[0], "original")
            path.unlink()


if __name__ == "__main__":
    unittest.main()
