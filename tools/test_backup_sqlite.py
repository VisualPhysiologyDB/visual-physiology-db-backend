"""Run with vpod_env: python tools/test_backup_sqlite.py. Uses temporary files only."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from backup_sqlite import backup


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.source = Path(self.folder.name) / 'source.sqlite3'
        self.destination = Path(self.folder.name) / 'snapshot.sqlite3'

    def test_committed_wal_records_are_in_snapshot(self):
        connection = sqlite3.connect(self.source)
        self.addCleanup(connection.close)
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA wal_autocheckpoint=0')
        connection.execute('CREATE TABLE observations (id INTEGER PRIMARY KEY, notes TEXT)')
        connection.execute("INSERT INTO observations VALUES (1, 'curated')")
        connection.commit()
        self.assertTrue(Path(str(self.source) + '-wal').exists())
        result = backup(self.source, self.destination)
        with sqlite3.connect(self.destination) as restored:
            self.assertEqual(restored.execute('SELECT * FROM observations').fetchall(), [(1, 'curated')])
        self.assertEqual(result['integrity_check'], 'ok')
        self.assertEqual(self.destination.stat().st_mode & 0o777, 0o600)
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM observations').fetchone()[0], 1)

    def test_refuses_existing_destination(self):
        self.source.touch()
        self.destination.write_text('preserve this backup')
        with self.assertRaises(FileExistsError):
            backup(self.source, self.destination)
        self.assertEqual(self.destination.read_text(), 'preserve this backup')

    def test_refuses_missing_source_without_creating_it(self):
        with self.assertRaises(FileNotFoundError):
            backup(self.source, self.destination)
        self.assertFalse(self.source.exists())
        self.assertFalse(self.destination.exists())

    def test_refuses_same_database(self):
        self.source.touch()
        with self.assertRaises(ValueError):
            backup(self.source, self.source)
        self.assertTrue(self.source.exists())

    def test_invalid_database_removes_incomplete_snapshot(self):
        self.source.write_text('not SQLite')
        with self.assertRaises(sqlite3.DatabaseError):
            backup(self.source, self.destination)
        self.assertFalse(self.destination.exists())


if __name__ == '__main__':
    unittest.main()
