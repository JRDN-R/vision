"""Deterministic engine acceptance checks. No provider APIs or model downloads."""
import base64
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from context_engine import ContextEngine, ContextError, MAX_BUDGET
from context_sources import ExtractionBudget, ExtractionBudgetExceeded, extract_source


def attachment(name, text, aid='a'):
    raw = text.encode() if isinstance(text,str) else text
    return dict(id=aid,name=name,mime='text/plain',data='data:text/plain;base64,'+base64.b64encode(raw).decode())


def board(files=(), *, prompt='Preserve exact facts and cite sources.'):
    return dict(title='Wing project',mainPrompt=prompt,nodes=[dict(id='wing',title='Wing',prompt='Keep RH and LH distinct.',attachments=list(files))],edges=[])


def operations(count=13):
    return '\n\n'.join(f'OPN {i*30:04d}\nWC 2CU0SA\nDescription: Inspect RH rib {i}\nRun Hrs: {i/10:.1f}\nNotes: Preserve 133-430065-2 / NA 01-1A-1. Exact note {i}.' for i in range(1,count+1))


class ContextEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = ContextEngine(self.temp.name, {'debounceSeconds':0})

    def tearDown(self):
        self.engine.close()
        self.temp.cleanup()

    def index(self, project=None, revision=1, owner='alice', pid='board'):
        return self.engine.index_project(owner,pid,revision,project or board([attachment('ops.txt',operations())]))

    def test_complete_thirteen_operation_enumeration(self):
        state = self.index()
        self.assertTrue(state['ready'])
        result = self.engine.prepare('alice','board',1,'Write the script reproducing every operation')
        self.assertTrue(result['complete'],result['warnings'])
        self.assertEqual(result['coverage']['includedRecordCount'],13)
        for i in range(1,14):
            self.assertIn(f'OPN {i*30:04d}',result['text'])
            self.assertIn(f'Exact note {i}.',result['text'])
        self.assertIn('133-430065-2',result['text'])

    def test_exact_identifier_search_retrieves_whole_record_group(self):
        self.index()
        result = self.engine.prepare('alice','board',1,'Find 133-430065-2 at OPN 0390')
        self.assertEqual(result['coverage']['includedRecordCount'],13)
        self.assertTrue(any('Exact note 13.' in item['text'] for item in result['items']))
        self.assertEqual(result['manifest']['semantic']['status'],'lexical-only')

    def test_incremental_changed_only_with_deleted_replaced_sources(self):
        value = board([attachment('one.txt','first','one'),attachment('two.txt','second','two')])
        first = self.index(value)
        self.assertEqual(first['manifest']['metrics']['indexedSources'],4)
        updated = copy.deepcopy(value)
        updated['nodes'][0]['attachments'][1] = attachment('two.txt','replacement','two')
        self.index(updated,2)
        metric = self.engine.status('alice','board',2)['manifest']['metrics']
        self.assertEqual(metric['indexedSources'],1)
        self.assertEqual(metric['reusedSources'],3)
        updated['nodes'][0]['attachments'].pop(0)
        self.index(updated,3)
        result = self.engine.prepare('alice','board',3,'everything',mode='full')
        self.assertNotIn('"text":"first"',result['text'])
        self.assertIn('replacement',result['text'])
        self.assertIn('first',self.engine.prepare('alice','board',1,'everything',mode='full')['text'])

    def test_revision_pin_cannot_change_content(self):
        self.index()
        with self.assertRaises(ContextError) as error:
            self.index(board([attachment('new.txt','new')]))
        self.assertEqual(error.exception.status_code,409)

    def test_missing_revision_never_falls_back_to_previous(self):
        self.index()
        result = self.engine.prepare('alice','board',2,'all operations')
        self.assertFalse(result['ready'])
        self.assertFalse(result['complete'])
        self.assertEqual(result['items'],[])
        self.assertTrue(self.engine.status('alice','board',1)['ready'])

    def test_pending_and_interrupted_jobs_recover(self):
        value = board([attachment('ops.txt',operations())])
        self.engine.enqueue('alice','board',1,value)
        with self.engine.db() as db:
            db.execute("UPDATE context_jobs SET status='processing'")
        self.engine.close()
        self.engine = ContextEngine(self.temp.name,{'debounceSeconds':0})
        self.assertTrue(self.engine.process_next())
        self.assertTrue(self.engine.status('alice','board',1)['ready'])
        self.assertFalse(self.engine.process_next())

    def test_restarted_engine_prevents_stale_worker_publication(self):
        value = board([attachment('ops.txt',operations())])
        self.engine.enqueue('alice','board',1,value)
        old_collect = self.engine._collect
        replacements = []
        def restart(*args):
            replacement = ContextEngine(self.temp.name,{'debounceSeconds':0})
            replacements.append(replacement)
            return old_collect(*args)
        with patch.object(self.engine,'_collect',side_effect=restart):
            self.engine.process_next()
        try:
            self.assertFalse(replacements[0].status('alice','board',1)['ready'])
            replacements[0].process_next()
            self.assertTrue(replacements[0].status('alice','board',1)['ready'])
        finally:
            replacements[0].close()

    def test_durable_snapshots_deduplicate_inline_payloads(self):
        value = board([attachment('large.txt','Long source line.\n'*2000)])
        self.index(value)
        updated = copy.deepcopy(value)
        updated['nodes'][0]['x']=200
        self.index(updated,2)
        with self.engine.db() as db:
            rows = db.execute('SELECT snapshot,snapshot_format FROM context_jobs').fetchall()
        self.assertTrue(all(row['snapshot_format']==1 for row in rows))
        self.assertTrue(all(len(row['snapshot'])<2000 for row in rows))
        self.assertNotIn('base64',rows[0]['snapshot'])
        self.assertEqual(self.engine.status('alice','board',2)['manifest']['metrics']['indexedSources'],0)

    def test_explicit_disabled_conditions_and_legacy_branches(self):
        value = dict(nodes=[dict(id=n) for n in ('a','b','c')],edges=[dict(**{'from':'a','to':'b'},condition='old retained condition',conditionEnabled=False),{'from':'a','to':'c'}])
        self.index(value)
        edges = self.engine.status('alice','board',1)['manifest']['edges']
        self.assertFalse(edges[0]['conditionEnabled'])
        self.assertEqual(edges[0]['condition'],'old retained condition')
        self.assertTrue(edges[1]['conditionEnabled'])

    def test_failure_keeps_previous_published_generation(self):
        self.index()
        with patch.object(self.engine,'_collect',side_effect=RuntimeError('do not expose sk-private-message')):
            self.index(board([attachment('other.txt','changed')]),2)
        self.assertTrue(self.engine.status('alice','board',1)['ready'])
        failed = self.engine.status('alice','board',2)
        self.assertEqual(failed['status'],'attention')
        self.assertNotIn('sk-private-message',failed['warning'])

    def test_project_text_budget_stops_new_generation_before_later_archives(self):
        self.engine.settings['maxProjectTextBytes']=4000
        self.index(board([attachment('small.txt','Safe first revision.')]))
        archives=[]
        for i in range(12):
            stream=io.BytesIO()
            with zipfile.ZipFile(stream,'w') as archive:
                archive.writestr('record.txt',f'Source {i}: '+('technical text '*35))
            archives.append(attachment(f'archive-{i}.zip',stream.getvalue(),str(i)))
        project=board(archives)
        untouched=copy.deepcopy(project)
        with patch('context_engine.extract_source',wraps=extract_source) as parser:
            state=self.index(project,2)
        self.assertEqual(state['status'],'attention')
        self.assertFalse(state['ready'])
        self.assertIn('derived-text budget exceeded',state['warning'])
        self.assertLess(parser.call_count,len(archives))
        self.assertEqual(project,untouched)
        self.assertTrue(self.engine.status('alice','board',1)['ready'])
        self.assertEqual(self.engine.prepare('alice','board',2,'all')['items'],[])
        with self.engine.db() as db:
            count=db.execute('SELECT COUNT(*) FROM context_chunks WHERE revision=2').fetchone()[0]
        self.assertEqual(count,0)

    def test_archive_budget_interrupts_member_processing_and_covers_utf8(self):
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            for i in range(50):
                archive.writestr(f'record-{i}.txt','é'*300)
        budget=ExtractionBudget(1800)
        with patch('context_sources.extract_source',wraps=extract_source) as parser:
            with self.assertRaises(ExtractionBudgetExceeded):
                extract_source(stream.getvalue(),'records.zip',{'_projectTextBudget':budget})
        self.assertLess(parser.call_count,50)
        self.assertLessEqual(budget.used,budget.limit)

    def test_cached_results_cannot_bypass_generation_budget(self):
        value=board([attachment(f'{i}.txt','Evidence '+str(i)+' '+'body '*300,str(i)) for i in range(5)])
        self.index(value)
        self.engine.settings['maxProjectTextBytes']=2500
        with patch('context_engine.extract_source',wraps=extract_source) as parser:
            state=self.index(value,2)
        self.assertEqual(state['status'],'attention')
        self.assertIn('derived-text budget exceeded',state['warning'])
        self.assertEqual(parser.call_count,0)

    def test_atomic_rebuild_keeps_old_generation_until_publish(self):
        self.index()
        self.engine.rebuild('alice','board',1)
        state = self.engine.status('alice','board',1)
        self.assertTrue(state['ready'])
        self.assertEqual(state['status'],'updating')
        self.assertTrue(self.engine.prepare('alice','board',1,'OPN 0390')['items'])
        self.engine.process_next()
        self.assertEqual(self.engine.status('alice','board',1)['status'],'ready')

    def test_pipeline_change_schedules_same_revision_rebuild(self):
        self.index()
        self.engine.settings['documentSettings']={'enabled':False,'profile':'updated'}
        stale = self.engine.prepare('alice','board',1,'operations')
        self.assertEqual(stale['status'],'updating')
        self.assertFalse(stale['ready'])
        self.assertTrue(self.engine.process_next())
        self.assertTrue(self.engine.status('alice','board',1)['ready'])

    def test_multiple_users_and_projects_are_isolated(self):
        self.index()
        self.index(board([attachment('private.txt','Other account evidence')]),owner='bob')
        self.assertNotIn('133-430065',self.engine.prepare('bob','board',1,'all')['text'])
        private = self.engine.prepare('alice','board',1,'all')['items'][-1]['sourceId']
        other = self.engine.original('bob','board',1,private)
        self.assertNotIn('133-430065',Path(other['path']).read_text())
        with self.assertRaises(ContextError):
            self.engine.original('alice','different',1,private)

    def test_graph_follows_ancestor_evidence_and_directed_conditions(self):
        value = dict(title='Dependencies',nodes=[
            dict(id='parent',title='Dimensions',attachments=[attachment('limits.txt','Upper bore limit 0.379 inches.')]),
            dict(id='middle',title='Instruction',prompt='Use predecessor dimensions.'),
            dict(id='child',title='Repair',attachments=[attachment('repair.txt','Target actuator HJK-9933 repair.')]),
            dict(id='other',title='Separate',attachments=[attachment('other.txt','irrelevant oranges')])],
            edges=[{'from':'parent','to':'middle','condition':'If crack exists','conditionEnabled':True},{'from':'middle','to':'child'}])
        self.index(value)
        result = self.engine.prepare('alice','board',1,'HJK-9933')
        self.assertIn('0.379',result['text'])
        self.assertIn('If crack exists',result['text'])
        self.assertNotIn('irrelevant oranges',result['text'])
        node = self.engine.node('alice','board',1,'child')
        self.assertIn('0.379',node['text'])

    def test_deduplication_preserves_all_provenance(self):
        self.index(board([attachment('first.txt','Shared exact evidence','one'),attachment('second.txt','Shared exact evidence','two')]))
        result = self.engine.prepare('alice','board',1,'all',mode='full')
        shared = [item for item in result['items'] if item['text']=='Shared exact evidence']
        self.assertEqual(len(shared),1)
        self.assertEqual(shared[0]['aliases'][0]['file'],'second.txt')
        self.assertEqual(result['coverage']['duplicateUnits'],1)

    def test_different_versions_not_deduplicated_and_conflicts_visible(self):
        self.index(board([attachment('first.txt','OPN 0030\nHours 1\nOPN 0060\nHours 2','a'),attachment('second.txt','OPN 0030\nHours 5\nOPN 0060\nHours 2','b')]))
        result = self.engine.prepare('alice','board',1,'all operations')
        self.assertIn('0030',result['coverage']['conflictingIdentifiers'])
        self.assertFalse(result['complete'])
        self.assertIn('Hours 1',result['text'])
        self.assertIn('Hours 5',result['text'])

    def test_budget_never_silently_truncates_records_or_overstates_coverage(self):
        self.index()
        result = self.engine.prepare('alice','board',1,'all operations',budget=3000)
        self.assertLessEqual(len(result['text']),3000)
        self.assertFalse(result['complete'])
        self.assertEqual(result['coverage']['uniqueIncludedUnits'],len(result['items']))
        self.assertEqual(result['coverage']['includedRecordCount'],sum(i['kind']=='record' for i in result['items']))
        self.assertEqual(result['coverage']['includedUnits']+result['coverage']['omittedCount'],result['coverage']['requiredUnits'])
        for item in result['items']:
            if item['kind']=='record':
                self.assertIn('Exact note',item['text'])

    def test_source_paging_eventually_supplies_every_unit(self):
        self.index()
        sid = next(s['id'] for s in self.engine.status('alice','board',1)['manifest']['sourceInventory'] if s['name']=='ops.txt')
        offset, refs, pages = 0,set(),0
        while offset is not None:
            result = self.engine.source('alice','board',1,sid,budget=4500,offset=offset)
            self.assertLessEqual(len(result['text']),4500)
            self.assertTrue(result['items'])
            refs.update(i['reference'] for i in result['items'])
            new = result['nextOffset']
            self.assertTrue(new is None or new>offset)
            offset = new
            pages += 1
            self.assertLess(pages,20)
        self.assertEqual(len(refs),13)

    def test_wrapped_har_node_prompt_is_source_and_redacted(self):
        har = json.dumps({'log':{'entries':[{'request':{'url':'https://example.test/path?token=TOPSECRET','headers':[{'name':'Cookie','value':'VERYSECRET'}]}}]}})
        value = board([])
        value['nodes'][0]['prompt']='Use this capture to find the endpoint.\n'+har
        self.index(value)
        result = self.engine.prepare('alice','board',1,'all')
        self.assertNotIn('TOPSECRET',result['text'])
        self.assertNotIn('VERYSECRET',result['text'])
        self.assertTrue(any(s['kind']=='source' for s in result['manifest']['sourceInventory']))
        self.assertTrue(any(i['kind']=='instruction' and 'Use this capture' in i['text'] for i in result['items']))

    def test_original_text_transfer_is_sanitized_original_stays_local(self):
        value = board([attachment('script.py','API_KEY="dont-send-this-value"\nprint("hello")')])
        self.index(value)
        sid = next(s['id'] for s in self.engine.status('alice','board',1)['manifest']['sourceInventory'] if s['name']=='script.py')
        original = self.engine.original('alice','board',1,sid)
        self.assertTrue(original['sanitized'])
        self.assertNotIn('dont-send-this-value',Path(original['path']).read_text())
        with self.engine.db() as db:
            path = db.execute('SELECT path FROM context_sources WHERE source=?',(sid,)).fetchone()['path']
        self.assertIn('dont-send-this-value',Path(path).read_text())

    def test_full_code_transfer_preserves_executable_whitespace(self):
        code = 'value = """\n'+('literal line\n'*400)+'"""\nprint(value)\n'
        self.index(board([attachment('full.py',code)]))
        sid = next(s['id'] for s in self.engine.status('alice','board',1)['manifest']['sourceInventory'] if s['name']=='full.py')
        original = self.engine.original('alice','board',1,sid)
        self.assertEqual(Path(original['path']).read_text(),code)
        self.assertFalse(original['secretsRedacted'])

    def test_unsupported_and_corrupt_sources_are_explicitly_incomplete(self):
        self.index(board([attachment('bad.pdf',b'not a pdf','bad'),attachment('unknown.dat',b'unknown','unknown')]))
        result = self.engine.prepare('alice','board',1,'all')
        self.assertFalse(result['complete'])
        self.assertTrue(result['warnings'])
        self.assertEqual(result['manifest']['sourceCount'],4)

    def test_indexing_never_calls_provider_or_network(self):
        with patch('socket.create_connection',side_effect=AssertionError('Network prohibited')):
            self.index()
            self.engine.prepare('alice','board',1,'Find wing operations')

    def test_context_digest_stable_across_identical_requests(self):
        self.index()
        one = self.engine.prepare('alice','board',1,'OPN 0030')
        two = self.engine.prepare('alice','board',1,'OPN 0030')
        self.assertEqual(one['metrics']['contextDigest'],two['metrics']['contextDigest'])
        self.assertEqual(one['metrics']['contextCharacters'],len(one['text']))

    def test_nested_zip_safe_and_complete(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('records.csv','OPN,WC,Hours\n0030,2CU0SA,1.5\n0060,2CU0SA,2.0')
        result = extract_source(stream.getvalue(),'fixture.zip')
        self.assertTrue(result['complete'])
        self.assertEqual(len(result['units']),2)
        self.assertIn('records.csv',result['units'][0]['location'])


@unittest.skipUnless(os.environ.get('VISION_TEST_EMBEDDING_MODEL'),'Install an offline model and set VISION_TEST_EMBEDDING_MODEL for real semantic checks.')
class RealSemanticTests(unittest.TestCase):
    def test_real_semantics_and_record_suffix(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = ContextEngine(directory,dict(debounceSeconds=0,embeddingModelPath=os.environ['VISION_TEST_EMBEDDING_MODEL'],embeddingPackagesPath=os.environ.get('VISION_TEST_EMBEDDING_PACKAGES')))
            try:
                value = board([attachment('a.txt','A joyful dog runs beside its owner in the park.','a'),attachment('b.txt','Database schema migration removes obsolete indexes.','b')])
                engine.index_project('owner','semantic',1,value)
                self.assertTrue(engine.embeddings.capability()['ready'])
                result = engine.prepare('owner','semantic',1,'a happy canine exercising outside')
                self.assertIn('joyful dog',result['text'])
                vectors = engine.embeddings.encode(['Ordinary information. '*400+' The aircraft mounting bracket is fractured and needs structural repair.','An airplane attachment fitting has cracked.','Chocolate cake dessert.'])
                self.assertGreater(engine.embeddings.similarity(vectors[0],vectors[1]),engine.embeddings.similarity(vectors[0],vectors[2])+.15)
            finally:
                engine.close()


if __name__=='__main__':
    unittest.main()
