"""Independent malicious-source and authenticated context boundary checks.

All provider transports are mocked. Secret markers below are synthetic and are
searched through the complete public result, including provenance metadata.
"""
import base64
import io
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
import zipfile

from context_sources import decode_data, extract_source, local_document_process, redact


def inline(raw, mime='text/plain'):
    return 'data:'+mime+';base64,'+base64.b64encode(raw).decode()


def sensitive_har():
    return {'log': {'entries': [{
        'request': {'method': 'POST',
                    'url': 'https://alice:USERINFO_SECRET@example.test/records?access_token=QUERY_SECRET&opn=0030#token=FRAGMENT_SECRET',
                    'headers': [{'name': 'Authorization', 'value': 'Bearer HEADER_SECRET'},
                                {'name': 'X-Api-Key', 'value': 'API_HEADER_SECRET'}],
                    'cookies': [{'name': 'session', 'value': 'COOKIE_SECRET'}],
                    'postData': {'text': json.dumps({'password': 'BODY_SECRET', 'nested': {'token': 'NESTED_SECRET'}, 'OPN': '0030'})}},
        'response': {'status': 200, 'content': {'encoding': 'base64',
                     'text': base64.b64encode(b'{"token":"ENCODED_SECRET"}').decode()}}
    }]}}


class SourceSecurityTests(unittest.TestCase):
    def test_har_and_export_wrappers_exclude_credentials_and_encoded_bodies(self):
        raw = json.dumps(sensitive_har())
        encoded = base64.b64encode(b'{"token":"ENCODED_SECRET"}').decode()
        for name, content in [('capture.har', raw), ('capture.json', raw),
                              ('har-source.txt', 'NETWORK SOURCE\n'+raw+'\nEND SOURCE'),
                              ('capture.md', '```json\n'+raw+'\n```')]:
            with self.subTest(name=name):
                result = extract_source(content.encode(), name)
                serialized = json.dumps(result)
                for secret in ('USERINFO_SECRET', 'QUERY_SECRET', 'FRAGMENT_SECRET',
                               'HEADER_SECRET', 'API_HEADER_SECRET', 'COOKIE_SECRET',
                               'BODY_SECRET', 'NESTED_SECRET', encoded):
                    self.assertNotIn(secret, serialized)
                self.assertIn('0030', serialized)
                self.assertIn('POST', serialized)
                self.assertFalse(result['complete'], 'omitted encoded evidence must be disclosed')
                self.assertTrue(result['warnings'])

    def test_unstructured_secret_fields_and_html_forms(self):
        source = '''const token = "TOKEN_SECRET";
id_token=ID_SECRET
csrf_token: CSRF_SECRET
Authorization='Bearer AUTH_SECRET'
<input type="hidden" name="csrf" value="FORM_SECRET">
<meta name="api-key" content="META_SECRET">
https://user:URL_SECRET@example.test/path?token=URL_QUERY_SECRET
OPN 0030 WC 2CU0SA Run Hrs 0.50 P/N 133-100314-4
'''
        cleaned = redact(source)
        self.assertNotIn('_SECRET', cleaned)
        self.assertIn('OPN 0030 WC 2CU0SA Run Hrs 0.50 P/N 133-100314-4', cleaned)
        result = extract_source(source.encode(), 'original.py')
        self.assertFalse(result['complete'], 'redacted code is not a complete executable original')
        self.assertTrue(any('removed' in warning for warning in result['warnings']))

    def test_malicious_zip_entries_are_not_extracted_or_reported_as_complete(self):
        names = ['../outside.txt', '/absolute.txt', 'C:\\private.txt', '..\\outside.txt']
        for name in names:
            with self.subTest(name=name):
                archive = io.BytesIO()
                with zipfile.ZipFile(archive, 'w') as target:
                    target.writestr(name, b'MUST_NOT_BE_INDEXED')
                result = extract_source(archive.getvalue(), 'unsafe.zip')
                self.assertFalse(result['complete'])
                self.assertTrue(result['warnings'])
                self.assertNotIn('MUST_NOT_BE_INDEXED', json.dumps(result))
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as target:
            info = zipfile.ZipInfo('link.txt')
            info.create_system = 3
            info.external_attr = (0o120777 << 16)
            target.writestr(info, '/etc/passwd')
        self.assertFalse(extract_source(archive.getvalue(), 'symlink.zip')['complete'])

    def test_compression_bomb_is_refused_before_indexing(self):
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as target:
            target.writestr('too-large.txt', b'Z' * (2 * 1024 * 1024))
        result = extract_source(archive.getvalue(), 'bomb.zip')
        self.assertFalse(result['complete'])
        self.assertEqual(result['units'], [])

    def test_non_inline_urls_never_trigger_file_or_network_reads(self):
        for payload in ('file:///etc/passwd', 'http://127.0.0.1/private', '/etc/passwd',
                        'data:text/plain;base64,not-valid!!'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                decode_data(payload)

    def test_malformed_har_and_unknown_file_have_explicit_incomplete_status(self):
        for name, raw in [('bad.har', b'[]'), ('bad.har', b'{"log":[]}'),
                          ('bad.har', b'{"log":{"entries":[null]}}'),
                          ('opaque.bin', b'\x00\x01\x02')]:
            with self.subTest(name=name, raw=raw):
                result = extract_source(raw, name)
                self.assertFalse(result['complete'])
                self.assertTrue(result['warnings'])

    def test_converter_timeout_and_shutdown_reap_process_without_logs(self):
        for stopped in (False, True):
            with self.subTest(stopped=stopped), tempfile.TemporaryDirectory() as temp:
                event = threading.Event()
                if stopped:
                    event.set()
                processes = []
                real_popen = subprocess.Popen
                def launch(*args, **kwargs):
                    process = real_popen(*args, **kwargs)
                    processes.append(process)
                    return process
                with patch('context_sources.subprocess.Popen', side_effect=launch):
                    with self.assertRaises(InterruptedError if stopped else ValueError):
                        local_document_process([sys.executable, '-c', 'import time; time.sleep(30)'],
                                               Path(temp), timeout=.15, stop_event=event)
                self.assertEqual(len(processes), 1)
                self.assertIsNotNone(processes[0].poll())
                self.assertEqual(list(Path(temp).iterdir()), [])

    def test_converter_output_limit_reaps_process(self):
        with tempfile.TemporaryDirectory() as temp:
            script = "from pathlib import Path; import time; Path('result.json').write_bytes(b'x'*(25*1024*1024)); time.sleep(30)"
            # A fixed test command writes only to the generated temporary path.
            script = script.replace("Path('result.json')", 'Path('+repr(str(Path(temp)/'result.json'))+')')
            with self.assertRaisesRegex(ValueError, 'output limit'):
                local_document_process([sys.executable, '-c', script], Path(temp), timeout=3)


class AccountContextSecurityTests(unittest.TestCase):
    """Exercise real signed-token route authorization; no external AI calls."""
    @classmethod
    def setUpClass(cls):
        import test_venture
        test_venture.VentureTests.setUpClass.__func__(cls)

    def setUp(self):
        import server
        import test_venture
        self.token = test_venture.VentureTests.token.__get__(self)
        self.headers = test_venture.VentureTests.headers.__get__(self)
        test_venture.VentureTests.setUp(self)
        self.server = server
        self.engine = self.service.context.engine
        self.board_id = 'context_security_board_001'
        self.board_path = '/api/projects/'+self.board_id
        self.board = {'title': 'Private inspection', 'mainPrompt': 'Use the specified exact references.',
                      'nodes': [{'id': 'n1', 'title': 'Source', 'prompt': '', 'attachments': [
                          {'id': 'a1', 'name': 'records.txt', 'mime': 'text/plain',
                           'data': inline(b'OPN 0030\nWC 2CU0SA\nRun Hrs 0.50\nALICE_EVIDENCE')}]}], 'edges': []}
        self.save_board(0)
        self.engine.index_project('firebase:alice', self.board_id, 1, self.board)

    def tearDown(self):
        import test_venture
        self.service.context.close()
        test_venture.VentureTests.tearDown(self)

    def save_board(self, revision, board=None):
        result = self.client.put(self.board_path, headers=self.a,
                                 json={'project': board or self.board, 'revision': revision})
        self.assertEqual(result.status_code, 200, result.json)
        return result

    def submit_context(self, binding=None, headers=None):
        options = {'clientRequestId': 'security-request-1', 'message': 'Reproduce every operation.',
                   'model': 'gpt-6-astra', 'runOptions': {'codeInterpreter': False},
                   'boardContext': binding or {'projectId': self.board_id, 'revision': 1}}
        return self.client.post('/api/projects/'+self.cid+'/runs', headers=headers or self.a,
                                data={'options': json.dumps(options)})

    def test_context_routes_require_account_and_independent_board_ownership(self):
        checks = [('get', '/context?revision=1', None),
                  ('post', '/context/search', {'revision': 1, 'query': 'ALICE_EVIDENCE'}),
                  ('get', '/context/sources/unknown?revision=1', None)]
        for method, suffix, body in checks:
            for headers, status in [({}, 401), (self.b, 404)]:
                with self.subTest(method=method, suffix=suffix, status=status):
                    response = getattr(self.client, method)(self.board_path+suffix, headers=headers,
                                                            **({'json': body} if body else {}))
                    self.assertEqual(response.status_code, status, response.json)
                    self.assertNotIn('ALICE_EVIDENCE', response.get_data(as_text=True))
        # Alice owns this conversation but cannot attach Bob's project to it.
        bob_id = 'context_security_bob_0001'
        self.assertEqual(self.client.put('/api/projects/'+bob_id, headers=self.b,
            json={'project': {'nodes': []}, 'revision': 0}).status_code, 200)
        with patch.object(self.service, 'call_json') as provider:
            denied = self.submit_context({'projectId': bob_id, 'revision': 1})
        self.assertEqual(denied.status_code, 404, denied.json)
        provider.assert_not_called()

    def test_invalid_requests_fail_closed_without_provider_work(self):
        for body in ([], 'not an object', {'revision': True}, {'revision': -1},
                     {'revision': 1, 'query': ['bad']}, {'revision': 1, 'query': 'x'*8001}):
            response = self.client.post(self.board_path+'/context/search', headers=self.a, json=body)
            self.assertEqual(response.status_code, 400, (body, response.json))
        with patch.object(self.service, 'call_json') as provider:
            for binding in ({'projectId': self.board_id, 'revision': 99},
                            {'projectId': self.board_id, 'revision': 1, 'owner': 'firebase:bob'},
                            {'projectId': '../not-a-project', 'revision': 1}):
                self.assertIn(self.submit_context(binding).status_code, (400, 404, 409))
        provider.assert_not_called()

    def test_pinned_revision_is_not_replaced_by_later_board_edits(self):
        accepted = self.submit_context()
        self.assertEqual(accepted.status_code, 202, accepted.json)
        row = self.service.row(accepted.json['runId'])
        revised = json.loads(json.dumps(self.board))
        revised['nodes'][0]['attachments'][0]['data'] = inline(b'OPN 9999\nNEW_REVISION_ONLY')
        self.save_board(1, revised)
        self.engine.index_project('firebase:alice', self.board_id, 2, revised)
        with patch.object(self.service, 'call_json') as provider:
            payload = self.service.prepare_payload(row, 'fake-key')
        serialized = json.dumps(payload)
        self.assertIn('ALICE_EVIDENCE', serialized)
        self.assertNotIn('NEW_REVISION_ONLY', serialized)
        provider.assert_not_called()

    def test_retired_conversation_cannot_submit_or_retrieve_context(self):
        deleted = self.client.delete('/api/venture/conversations/'+self.cid, headers=self.a)
        self.assertEqual(deleted.status_code, 200, deleted.json)
        self.assertEqual(self.submit_context().status_code, 404)
        self.assertEqual(self.client.get('/api/projects/'+self.cid+'/context', headers=self.a).status_code, 404)
        # Deleting a standalone conversation must not delete the editable board.
        self.assertEqual(self.client.get(self.board_path, headers=self.a).status_code, 200)

    def test_full_source_transfer_sanitizes_har_and_reports_omitted_content(self):
        revised = json.loads(json.dumps(self.board))
        raw = json.dumps(sensitive_har()).encode()
        revised['nodes'][0]['attachments'] = [{'id': 'capture', 'name': 'network.txt', 'mime': 'text/plain',
                                              'data': inline(b'CAPTURE EXPORT\n'+raw)}]
        self.save_board(1, revised)
        self.engine.index_project('firebase:alice', self.board_id, 2, revised)
        rid = self.submit_context({'projectId': self.board_id, 'revision': 2, 'mode': 'full'}).json['runId']
        row = self.service.row(rid)
        row['run_options_json'] = json.dumps({'codeInterpreter': True})
        source = next(item['id'] for item in self.engine.status('firebase:alice', self.board_id, 2)['manifest']['sourceInventory']
                      if item['name'] == 'network.txt')
        sent = []
        def upload(key, method, path, **kwargs):
            self.assertEqual((method, path), ('POST', '/files'))
            sent.append(kwargs['files']['file'][1].read())
            return {'id': 'file_safe_context'}
        binding = self.service.context.binding(row)
        with patch.object(self.service, 'call_json', side_effect=upload):
            result, visuals = self.service.context.transfer(row, binding, source, 'fake-key')
            cached, _ = self.service.context.transfer(row, binding, source, 'fake-key')
        self.assertEqual(len(sent), 1, 'unchanged selected evidence should not upload twice')
        self.assertNotIn(b'_SECRET', sent[0])
        self.assertNotIn(base64.b64encode(b'{"token":"ENCODED_SECRET"}'), sent[0])
        self.assertIn(b'0030', sent[0])
        for response in (result, cached):
            self.assertTrue(response['transferred'])
            self.assertTrue(response['sanitized'])
            self.assertFalse(response['complete'])
            self.assertTrue(response['warnings'])
        self.assertEqual(visuals, [])
        # Sanitization never rewrites the account's original saved attachment.
        saved = self.client.get(self.board_path, headers=self.a).json['project']
        self.assertEqual(saved, revised)

    def test_spoofed_image_mime_cannot_bypass_source_transfer_validation(self):
        revised = json.loads(json.dumps(self.board))
        revised['nodes'][0]['attachments'] = [{'id': 'spoof', 'name': 'picture.png', 'mime': 'image/png',
                                              'data': inline(json.dumps(sensitive_har()).encode(), 'image/png')}]
        self.save_board(1, revised)
        self.engine.index_project('firebase:alice', self.board_id, 2, revised)
        rid = self.submit_context({'projectId': self.board_id, 'revision': 2}).json['runId']
        row = self.service.row(rid)
        row['run_options_json'] = json.dumps({'codeInterpreter': True})
        source = next(item['id'] for item in self.engine.status('firebase:alice', self.board_id, 2)['manifest']['sourceInventory']
                      if item['name'] == 'picture.png')
        call = {'name': 'vision_context', 'arguments': json.dumps({'operation': 'original', 'query': '', 'reference': source})}
        with patch.object(self.service, 'call_json') as provider:
            result, visuals = self.service.context.execute(row, call, 'fake-key')
        provider.assert_not_called()
        self.assertIn('error', result)
        self.assertEqual(visuals, [])


class EngineBoundaryTests(unittest.TestCase):
    def setUp(self):
        from context_engine import ContextEngine
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.engine = ContextEngine(self.root, {'debounceSeconds': 0})

    def tearDown(self):
        self.engine.close()
        self.temp.cleanup()

    def test_public_metadata_is_sanitized_including_timestamps_and_node_kinds(self):
        board = {'title': 'https://u:TITLE_SECRET@example.test/?token=TITLE_QUERY_SECRET',
                 'nodes': [{'id': 'one', 'kind': "token='KIND_SECRET'", 'title': 'Node',
                     'attachments': [{'id': 'one', 'name': 'source.txt', 'mime': 'text/plain',
                         'timestamp': 'access_token=TIME_SECRET', 'data': inline(b'Exact value 0.379')}]}], 'edges': []}
        self.engine.index_project('alice', 'project', 1, board)
        result = self.engine.prepare('alice', 'project', 1, 'all facts')
        self.assertNotIn('_SECRET', json.dumps(result))
        self.assertIn('0.379', result['text'])

    def test_excessive_manifest_and_final_coverage_obey_bounded_output(self):
        board = {'nodes': [{'id': 'node-'+str(i), 'title': 'X'*150+str(i), 'attachments': [
                    {'id': 'source', 'name': 'long-source-name-'+str(i)+'.txt', 'mime': 'text/plain',
                     'data': inline(('OPN '+str(1000+i)+'\n'+'record value '+str(i)).encode())}]}
                           for i in range(210)], 'edges': []}
        self.engine.index_project('alice', 'project', 1, board)
        result = self.engine.prepare('alice', 'project', 1, 'every record', budget=8000)
        self.assertLessEqual(len(result['text']), 8000)
        self.assertLessEqual(len(json.dumps(result['manifest'])), 8000,
                             'the separately returned manifest must not bypass the evidence budget')
        coverage = result['coverage']
        represented = sum(1+len(item.get('aliases', [])) for item in result['items'])
        self.assertEqual(coverage['includedUnits'], represented)
        self.assertEqual(coverage['uniqueIncludedUnits'], len(result['items']))
        self.assertEqual(coverage['omittedCount'], coverage['requiredUnits']-represented)
        self.assertFalse(result['complete'])

    def test_interrupted_job_recovers_and_invalid_new_revision_keeps_prior_generation(self):
        from context_engine import ContextEngine
        original = {'nodes': [{'id': 'one', 'caption': 'OLD_EXACT_FACT 0030'}], 'edges': []}
        self.engine.index_project('alice', 'project', 1, original)
        revised = {'nodes': [{'id': 'one', 'caption': 'NEW_EXACT_FACT 0090'}], 'edges': []}
        self.engine.enqueue('alice', 'project', 2, revised)
        with self.engine.db() as db:
            db.execute("UPDATE context_jobs SET status='processing' WHERE revision=2")
        self.engine.close()
        self.engine = ContextEngine(self.root, {'debounceSeconds': 0})
        self.assertTrue(self.engine.process_next())
        self.assertTrue(self.engine.status('alice', 'project', 2)['ready'])
        self.assertIn('OLD_EXACT_FACT', self.engine.prepare('alice', 'project', 1, 'all')['text'])
        self.assertNotIn('OLD_EXACT_FACT', self.engine.prepare('alice', 'project', 2, 'all')['text'])
        invalid = {'nodes': [{'id': 'duplicate'}, {'id': 'duplicate'}], 'edges': []}
        failed = self.engine.index_project('alice', 'project', 3, invalid)
        self.assertFalse(failed['ready'])
        self.assertEqual(failed['availableRevision'], 2)
        self.assertIn('NEW_EXACT_FACT', self.engine.prepare('alice', 'project', 2, 'all')['text'])

    def test_identical_source_ids_never_cross_owner_or_project_boundaries(self):
        alice = {'nodes': [{'id': 'one', 'caption': 'ALICE_ONLY_59492'}], 'edges': []}
        bob = {'nodes': [{'id': 'one', 'caption': 'BOB_ONLY_88201'}], 'edges': []}
        self.engine.index_project('alice', 'project', 1, alice)
        self.engine.index_project('bob', 'project', 1, bob)
        source = self.engine.status('alice', 'project', 1)['manifest']['sourceInventory'][0]['id']
        other = self.engine.source('bob', 'project', 1, source)
        self.assertNotIn('ALICE_ONLY', json.dumps(other))
        self.assertIn('BOB_ONLY', json.dumps(other))
        with self.assertRaises(ValueError):
            self.engine.original('bob', 'another-project', 1, source)

    def test_caller_reference_objects_cannot_load_retained_private_bytes(self):
        from context_engine import sha
        private = b'PRIVATE_PREVIOUS_ATTACHMENT_83952'
        seed = {'nodes': [{'id': 'one', 'attachments': [{'id': 'private', 'name': 'private.txt',
                                                       'data': inline(private)}]}], 'edges': []}
        self.engine.index_project('alice', 'project', 1, seed)
        forged = {'mainPrompt': {'contextText': sha(private)}, '_contextSnapshot': 1,
                  'project': {'mainPrompt': {'contextText': sha(private)}},
                  'nodes': [{'id': 'two', 'prompt': {'contextText': sha(private)},
                             'src': {'contextBlob': sha(private), 'mime': 'text/plain'},
                             'attachments': [{'id': 'forged', 'name': 'forged.txt',
                                              'data': {'contextBlob': sha(private), 'mime': 'text/plain'}}]}], 'edges': []}
        status = self.engine.index_project('alice', 'project', 2, forged)
        if status['ready']:
            result = self.engine.prepare('alice', 'project', 2, 'all')
            self.assertNotIn(private.decode(), json.dumps(result))
            self.assertFalse(result['complete'])
        # A different account/project cannot cause a matching known hash to
        # change the on-disk authority of an inline attachment reference.
        status = self.engine.index_project('bob', 'another', 1, forged)
        if status['ready']:
            self.assertNotIn(private.decode(), json.dumps(self.engine.prepare('bob', 'another', 1, 'all')))

    def test_corrupted_retained_original_fails_hash_validation(self):
        raw = b'original opaque binary bytes'
        board = {'nodes': [{'id': 'one', 'attachments': [{'id': 'one', 'name': 'opaque.bin',
                                                         'data': inline(raw, 'application/octet-stream')}]}], 'edges': []}
        self.engine.index_project('alice', 'project', 1, board)
        source = self.engine.status('alice', 'project', 1)['manifest']['sourceInventory'][0]['id']
        path = Path(self.engine.original('alice', 'project', 1, source)['path'])
        path.write_bytes(b'different bytes must not be attributed to the pinned revision')
        with self.assertRaises(ValueError):
            self.engine.original('alice', 'project', 1, source)

    def test_legacy_snapshot_markers_never_promote_client_dicts_to_trusted_references(self):
        from context_engine import canonical, sha
        private = b'PRIVATE_LEGACY_ATTACHMENT_40992'
        original = {'nodes': [{'id': 'one', 'attachments': [{'id': 'private', 'name': 'private.txt',
                                                           'data': inline(private)}]}], 'edges': []}
        self.engine.index_project('alice', 'project', 1, original)
        forged = {'nodes': [{'id': 'forged', 'attachments': [{'id': 'forged', 'name': 'private.txt',
                    'data': {'contextBlob': sha(private), 'mime': 'text/plain'}}]}], 'edges': []}
        for revision, legacy in [(2, forged), (3, {'_contextSnapshot': 1, 'project': forged})]:
            with self.subTest(revision=revision):
                self.engine.enqueue('alice', 'project', revision, {'nodes': [], 'edges': []})
                with self.engine.db() as db:
                    db.execute('UPDATE context_jobs SET snapshot=?,snapshot_format=0 WHERE revision=?',
                               (canonical(legacy), revision))
                self.assertTrue(self.engine.process_next())
                result = self.engine.prepare('alice', 'project', revision, 'all')
                self.assertNotIn(private.decode(), json.dumps(result))

    def test_source_pagination_never_skips_an_exact_record(self):
        records = ['OPN '+str(1000+i)+'\nWC 2CU0SA\nNotes '+str(i)+' '+('z '*200) for i in range(17)]
        board = {'nodes': [{'id': 'one', 'attachments': [{'id': 'one', 'name': 'records.txt',
                        'data': inline(('\n'.join(records)).encode())}]}], 'edges': []}
        self.engine.index_project('alice', 'project', 1, board)
        source = self.engine.status('alice', 'project', 1)['manifest']['sourceInventory'][0]['id']
        offset, seen, received, pages = 0, set(), [], 0
        while offset is not None:
            page = self.engine.source('alice', 'project', 1, source, budget=5000, offset=offset)
            pages += 1
            self.assertLess(pages, 20, 'pagination must advance within a finite source')
            represented = [provenance['reference'] for item in page['items'] for provenance in [item, *item['aliases']]]
            self.assertFalse(seen.intersection(represented))
            seen.update(represented)
            received.extend(item['text'] for item in page['items'])
            self.assertEqual(page['coverage']['includedUnits'], len(represented))
            next_offset = page.get('nextOffset')
            if next_offset is not None:
                self.assertGreater(next_offset, offset, 'a fitting record must advance the cursor')
            offset = next_offset
        self.assertGreater(pages, 1)
        self.assertEqual(len(seen), 17)
        for i in range(17):
            self.assertIn('OPN '+str(1000+i), '\n'.join(received))


if __name__ == '__main__':
    unittest.main()
