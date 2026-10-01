"""Focused offline checks. No YouTube, Gemini, or OpenAI requests are sent."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import server
from media import normalize_url, parse_captions, snapshot_interval


class ProcessorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        config = self.root / 'config.json'
        config.write_text(json.dumps({'token': 'a' * 48, 'backendUrl': 'https://vision-test.example.ts.net'}))
        server.configure(config)
        server.app.config['TESTING'] = True
        self.client = server.app.test_client()
        self.headers = {'Authorization': 'Bearer ' + 'a' * 48, 'Origin': 'null'}

    def tearDown(self):
        self.directory.cleanup()

    def start_job(self, request_id='test-1'):
        response = self.client.post('/api/youtube', json={'url': 'https://youtu.be/abcdefghijk', 'clientRequestId': request_id}, headers=self.headers)
        self.assertEqual(response.status_code, 202)
        return response.json['id']

    def test_authentication_and_cors(self):
        self.assertEqual(self.client.get('/api/health').status_code, 401)
        response = self.client.get('/api/health', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Access-Control-Allow-Origin'], 'null')
        denied = {**self.headers, 'Origin': 'https://other.example'}
        self.assertEqual(self.client.get('/api/health', headers=denied).status_code, 403)
        response = self.client.options('/api/youtube', headers={'Origin': 'https://jrdn-r.github.io', 'Access-Control-Request-Private-Network': 'true'})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers['Access-Control-Allow-Private-Network'], 'true')

    def test_idempotency_and_restart(self):
        job_id = self.start_job()
        self.assertEqual(self.start_job(), job_id)
        with server.connect_db() as db:
            db.execute("UPDATE jobs SET status='processing',attempts=1 WHERE id=?", (job_id,))
        leftover = self.root / 'data' / 'temporary' / 'vision-youtube-interrupted'
        leftover.mkdir()
        (leftover / 'source.mp4').write_bytes(b'private media')
        server.recover_jobs()
        self.assertEqual(server.get_job(job_id)['status'], 'queued')
        self.assertFalse(leftover.exists())
        with server.connect_db() as db:
            db.execute("UPDATE jobs SET status='processing',attempts=3 WHERE id=?", (job_id,))
        server.recover_jobs()
        self.assertEqual(server.get_job(job_id)['status'], 'error')

    def test_result_cleanup_and_receipt(self):
        job_id = self.start_job()
        def fake_process(job, url, ffmpeg, update, **kwargs):
            update(job, status='complete', phase='Ready', progress=100, result=b'{"frames":[],"transcript":{"text":"hello"}}')
        with patch.object(server, 'process_job', fake_process):
            self.assertTrue(server.process_next_job())
        response = self.client.get('/api/jobs/' + job_id + '/result', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['transcript']['text'], 'hello')
        self.assertEqual(self.client.delete('/api/jobs/' + job_id, headers=self.headers).status_code, 200)
        self.assertFalse(list((self.root / 'data' / 'results').iterdir()))
        self.assertEqual(self.client.get('/api/jobs/' + job_id + '/result', headers=self.headers).status_code, 410)
        self.assertEqual(self.start_job(), job_id)

    def test_urls_timing_and_captions(self):
        expected = 'https://www.youtube.com/watch?v=abcdefghijk'
        for url in ('youtu.be/abcdefghijk?t=3', 'https://youtube.com/shorts/abcdefghijk', expected):
            self.assertEqual(normalize_url(url), expected)
        for url in ('https://youtube.com.evil.example/watch?v=abcdefghijk', 'file:///tmp/video', 'https://youtube.com:8443/watch?v=abcdefghijk'):
            with self.assertRaises(ValueError):
                normalize_url(url)
        self.assertEqual([snapshot_interval(v) for v in (3, 5, 299, 300, 599, 600)], [1, 1, 5, 30, 30, 60])
        self.assertEqual(parse_captions('WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello there\n', 'vtt'), '[00:00:01.000] Hello there')

    def test_openai_payload_and_session_ownership(self):
        payload = server.response_payload('file_test', {'model': 'gpt-6-astra', 'max_output_tokens': 999999})
        self.assertEqual(payload['tools'][0]['container']['file_ids'], ['file_test'])
        self.assertEqual(payload['tool_choice'], 'required')
        self.assertEqual(payload['max_output_tokens'], 64000)
        server.track_event({'type': 'response.created', 'response': {'id': 'resp_test', 'status': 'in_progress'}}, 'installation-owner')
        with server.app.test_request_context(headers=self.headers):
            server.authorize()
            self.assertEqual(server.own_document('visionResponses', 'resp_test')[1]['status'], 'in_progress')
            with self.assertRaises(server.APIError):
                server.own_document('visionResponses', 'resp_unknown')
        response = self.client.post('/api/openai/run', headers=self.headers,
                                    data={'file': (io.BytesIO(b'not an archive'), 'bad.zip')})
        self.assertEqual(response.status_code, 400)


if __name__ == '__main__':
    unittest.main()
