import tempfile
import unittest
from pathlib import Path
from quantfoundry.storage.database import Database
from quantfoundry.storage.comments import sync_comments


class CommentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=Database(Path(self.tmp.name)/"test.db");self.db.initialize()

    def test_every_actual_table_and_column_documented(self):
        with self.db.connection() as c:
            expected=set()
            for row in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
                table=row[0];expected.add((table,""))
                expected.update((table,col[0]) for col in c.execute("SELECT name FROM pragma_table_info(?)",(table,)))
            actual={(r[0],r[1]) for r in c.execute("SELECT table_name,column_name FROM schema_comments WHERE length(comment)>0")}
            self.assertEqual(expected,actual)
            self.assertNotIn(("instruments","in_current_listing"),actual)

    def test_idempotent_timestamps(self):
        with self.db.transaction() as c:c.execute("UPDATE schema_comments SET updated_at='2000-01-01 00:00:00'")
        self.db.initialize()
        with self.db.connection() as c:
            self.assertEqual({r[0] for r in c.execute("SELECT updated_at FROM schema_comments")},{"2000-01-01 00:00:00"})

    def test_v2_extra_legacy_column_and_data_preserved(self):
        with self.db.transaction() as c:
            c.execute("DROP TABLE schema_comments")
            c.execute("ALTER TABLE instruments ADD COLUMN in_current_listing INTEGER DEFAULT 1")
            c.execute("INSERT INTO stock VALUES('NYSE','A','2024-01-01',1)")
            c.execute("PRAGMA user_version=2")
        self.db.initialize()
        with self.db.connection() as c:
            self.assertEqual(c.execute("SELECT ticker FROM stock").fetchone()[0],"A")
            self.assertIsNotNone(c.execute("SELECT comment FROM schema_comments WHERE table_name='instruments' AND column_name='in_current_listing'").fetchone())
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0],3)

    def test_unknown_column_fails_and_rolls_back(self):
        with self.assertRaises(ValueError):
            with self.db.transaction() as c:
                c.execute("ALTER TABLE stock ADD COLUMN undocumented TEXT")
                sync_comments(c)
        with self.db.connection() as c:
            self.assertNotIn('undocumented',[r[0] for r in c.execute("SELECT name FROM pragma_table_info('stock')")])
