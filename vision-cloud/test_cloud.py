"""Focused tests with no cloud credentials or third-party SDK installation."""
import ast
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
import media

SOURCE = ast.parse(Path(__file__).with_name('app.py').read_text())

def load_functions(*names, **values):
    nodes = [node for node in SOURCE.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    for node in nodes:
        if isinstance(node, ast.FunctionDef):
            node.decorator_list = []
    scope = {'re': re, **values}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'app.py', 'exec'), scope)
    return scope

class CloudChecks(unittest.TestCase):
    def test_video_links_and_timing(self):
        self.assertEqual(media.normalize_url('https://youtu.be/abcdefghijk?si=abc'), 'https://www.youtube.com/watch?v=abcdefghijk')
        self.assertEqual(media.normalize_url('https://youtube.com/shorts/abcdefghijk'), 'https://www.youtube.com/watch?v=abcdefghijk')
        for invalid in ['https://example.com/watch?v=abcdefghijk', 'https://youtube.com@localhost/watch?v=abcdefghijk', 'http://127.0.0.1/video']:
            with self.assertRaises(ValueError):
                media.normalize_url(invalid)
        self.assertEqual([media.snapshot_interval(n) for n in (3, 5, 299, 300, 599, 600, 7200)], [1, 1, 5, 30, 30, 60, 60])

    def test_caption_timestamps(self):
        text = media.parse_captions('{"events":[{"tStartMs":1250,"segs":[{"utf8":"Hello there"}]}]}', 'json3')
        self.assertEqual(text, '[00:00:01.250] Hello there')

    def test_direct_code_interpreter_request(self):
        scope = load_functions('APIError', 'response_payload', BASE_INSTRUCTIONS='Content-only instructions')
        payload = scope['response_payload']('file_test', {'model': 'gpt-6-astra', 'message': 'Summarize', 'max_output_tokens': 4000})
        self.assertEqual(payload['tools'], [{'type': 'code_interpreter', 'container': {'type': 'auto', 'memory_limit': '1g', 'file_ids': ['file_test']}}])
        self.assertEqual(payload['tool_choice'], 'required')
        self.assertTrue(payload['background'] and payload['stream'])
        self.assertEqual(payload['reasoning'], {'summary': 'auto'})
        self.assertEqual(payload['input'], 'Summarize')
        self.assertEqual(payload['max_output_tokens'], 4000)

    def test_auth_does_not_treat_null_origin_as_permission(self):
        req = SimpleNamespace(headers={'Origin': 'null'}, method='GET', path='/api/jobs/test')
        claims = {'uid': 'owner'}
        calls = []
        def verify(token, **kwargs):
            calls.append((token, kwargs))
            return claims
        scope = load_functions('APIError', 'authorize', request=req, ORIGINS={'null', 'https://jrdn-r.github.io'},
                               OWNER_UIDS={'owner'}, auth=SimpleNamespace(verify_id_token=verify), firebase_app=lambda: 'app', g=SimpleNamespace())
        with self.assertRaises(scope['APIError']) as caught:
            scope['authorize']()
        self.assertEqual(caught.exception.status, 401)
        req.headers['Authorization'] = 'Bearer signed-token'
        scope['authorize']()
        self.assertEqual(scope['g'].uid, 'owner')
        self.assertTrue(calls[0][1]['check_revoked'])
        claims['uid'] = 'someone-else'
        with self.assertRaises(scope['APIError']) as caught:
            scope['authorize']()
        self.assertEqual(caught.exception.status, 403)
        req.headers['Origin'] = 'https://unrelated.example'
        with self.assertRaises(scope['APIError']) as caught:
            scope['authorize']()
        self.assertEqual(caught.exception.status, 403)

if __name__ == '__main__':
    unittest.main()
