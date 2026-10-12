import json
import tempfile
import unittest
from context_engine import ContextEngine, encode_cache, decode_cache, ContextError
from context_sources import ExtractionBudget, ExtractionBudgetExceeded


class CompressionTests(unittest.TestCase):
    def test_exact_roundtrip_and_compression_with_repeated_numbers_and_negations(self):
        source={'units':[{'text':'Do not enlarge 0.380 inch bore. RH/LH '+('repeat '*100)}]*100}
        packed=encode_cache(source)
        self.assertIsInstance(packed,bytes)
        self.assertLess(len(packed),len(json.dumps(source))//10)
        self.assertEqual(decode_cache(packed,ExtractionBudget(1000000)),source)
        with self.assertRaises(ExtractionBudgetExceeded):
            decode_cache(packed,ExtractionBudget(1024))
        with self.assertRaises(ContextError):
            decode_cache(packed[:-2],ExtractionBudget(1000000))

    def test_real_index_progress_and_cache_reuse(self):
        with tempfile.TemporaryDirectory() as root:
            engine=ContextEngine(root,{'debounceSeconds':0})
            project={'nodes':[{'id':'facts','title':'Exact facts','prompt':'Do not enlarge 0.380 inch RH bore. '*100}]}
            observed=[]
            original=engine._progress
            def capture(job,stage,completed=None,total=None):
                original(job,stage,completed,total)
                observed.append(engine.status('owner','project',1)['progress'])
            engine._progress=capture
            ready=engine.index_project('owner','project',1,project)
            self.assertTrue(ready['ready'],ready)
            self.assertEqual(ready['progress']['stage'],'Ready')
            self.assertTrue(any(p['stage']=='Extracting sources' and p['total']>0 for p in observed))
            self.assertTrue(all(p.get('estimatedRemainingSeconds') is None for p in observed))
            second=engine.index_project('owner','project',2,project)
            self.assertGreater(second['manifest']['metrics']['reusedSources'],0)
            self.assertFalse(second['indexingRequiresAnswerModel'])


if __name__=='__main__':
    unittest.main()
