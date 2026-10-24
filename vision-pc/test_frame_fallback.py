"""Pure, offline regression tests for automatic provider transitions."""
import sqlite3
import unittest
from frame_fallback import advance, recover_inflight


class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE frame_collections (
            id TEXT PRIMARY KEY, caption_engine TEXT, fallback_enabled INTEGER,
            fallback_history_json TEXT DEFAULT '[]', status TEXT,analysis_step INTEGER,
            retry_at REAL DEFAULT 0,error TEXT,luna_inflight INTEGER DEFAULT 0)""")

    def tearDown(self):
        self.db.close()

    def add(self, ident='vid', engine='luna', enabled=True, status='processing'):
        self.db.execute("INSERT INTO frame_collections(id,caption_engine,fallback_enabled,status,analysis_step) VALUES(?,?,?,?,?)",
                        (ident,engine,int(enabled),status,1))

    def test_luna_to_gemini_to_qwen_without_retry(self):
        self.add()
        self.assertEqual(advance(self.db,'vid','luna','403 upstream'), 'gemini')
        row=self.db.execute('SELECT * FROM frame_collections').fetchone()
        self.assertEqual((row['status'],row['caption_engine']), ('queued','gemini'))
        self.assertIsNone(advance(self.db,'vid','luna','stale retry'))
        self.assertEqual(advance(self.db,'vid','gemini','Gemini quota unavailable'), 'local')
        row=self.db.execute('SELECT * FROM frame_collections').fetchone()
        self.assertEqual((row['status'],row['caption_engine'],row['analysis_step']),('queued','local',1))
        import json
        transitions=json.loads(row['fallback_history_json'])
        self.assertEqual([(x['from'],x['to']) for x in transitions],
                         [('luna','gemini'),('gemini','local')])

    def test_explicit_luna_only_pauses(self):
        self.add(enabled=False)
        self.assertIsNone(advance(self.db,'vid','luna','API disabled'))
        row=self.db.execute('SELECT * FROM frame_collections').fetchone()
        self.assertEqual((row['status'],row['caption_engine']), ('paused','luna'))

    def test_restart_skips_uncertain_provider(self):
        self.add('a','luna',True)
        self.add('b','gemini',True)
        self.add('c','luna',False)
        self.add('d','local',True)
        recover_inflight(self.db)
        rows={x['id']:x for x in self.db.execute('SELECT * FROM frame_collections')}
        self.assertEqual(rows['a']['caption_engine'],'gemini')
        self.assertEqual(rows['b']['caption_engine'],'local')
        self.assertEqual(rows['c']['status'],'paused')
        self.assertEqual(rows['d']['caption_engine'],'local')
        self.assertIn('uncertain', rows['a']['fallback_history_json'])

if __name__ == '__main__':
    unittest.main()
