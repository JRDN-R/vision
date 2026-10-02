"""Offline uploaded-video tests; no provider, YouTube, or other network calls."""
import io
import array
import math
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import server
import uploaded_media as media


class UploadedMediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ffmpeg = self.root / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
        self.ffmpeg.touch()
        self.ffmpeg.with_name('ffprobe.exe' if os.name == 'nt' else 'ffprobe').touch()
        config = self.root / 'config.json'
        config.write_text(json.dumps({'token': 'a'*48, 'publicAccess': True, 'ffmpeg': str(self.ffmpeg)}))
        server.configure(config)
        self.client, self.service = server.app.test_client(), server.uploaded_media
        server.app.config['TESTING'] = True
        self.service.stop.clear()
        self.headers = {'Authorization': 'Bearer '+'a'*48, 'X-Vision-Project-Key': 'k'*48, 'Origin': 'null'}
        self.base = '/api/projects/project_video_test1'
        self.client.put(self.base, json={'project': {}, 'revision': 0}, headers=self.headers)

    def tearDown(self):
        self.tmp.cleanup()

    def submit(self, content=b'video', request_id='video1', base=None, headers=None, name='clip.mp4'):
        return self.client.post((base or self.base)+'/media', data={'requestId': request_id, 'file': (io.BytesIO(content), name)},
                                headers=self.headers if headers is None else headers)

    def status(self, job, base=None, headers=None):
        return self.client.get((base or self.base)+'/media/'+job, headers=self.headers if headers is None else headers)

    def fake_processing(self, value):
        directory = self.service.root / value['id']
        (directory/'preview.mp4').write_bytes(b'compact-preview')
        (directory/'thumbnail.jpg').write_bytes(b'jpeg')
        (directory/'audio-000.mp3').write_bytes(b'ID3speech')
        (directory/'result.json').write_text(json.dumps({'duration': 1, 'frames': [], 'audioSections': []}))

    def test_project_authentication_and_complete_result_ownership(self):
        self.assertEqual(self.submit(headers={}).status_code, 401)
        self.assertEqual(self.submit(headers={**self.headers, 'X-Vision-Project-Key': 'x'*48}).status_code, 403)
        self.assertEqual(self.submit(base='/api/projects/project_missing_1').status_code, 404)
        job = self.submit().json['id']
        other = '/api/projects/project_video_test2'
        self.client.put(other, json={'project': {}, 'revision': 0}, headers=self.headers)
        with patch.object(self.service, 'process', side_effect=self.fake_processing):
            self.assertTrue(self.service.work_once())
        for suffix in ('', '/result', '/preview', '/thumbnail', '/audio/0'):
            self.assertEqual(self.client.get(other+'/media/'+job+suffix, headers=self.headers).status_code, 404)
            self.assertEqual(self.client.get(self.base+'/media/'+job+suffix, headers={'Authorization': 'Bearer '+'a'*48}).status_code, 403)
        self.assertEqual(self.client.delete(other+'/media/'+job, headers=self.headers).status_code, 404)
        response = self.client.get(self.base+'/media/'+job+'/preview', headers={**self.headers, 'Range': 'bytes=0-6'})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.data, b'compact')
        response.close()
        self.assertNotIn(str(self.root), json.dumps(self.status(job).json))
        self.assertTrue(self.status(job).json['resultReady'])

    def test_idempotency_content_conflicts_and_input_cleanup(self):
        accepted = self.submit(name='../../private.mov')
        self.assertEqual(accepted.status_code, 202, accepted.json)
        job = accepted.json['id']
        self.assertEqual(accepted.json['sourceName'], 'private.mov')
        self.assertEqual(self.submit().json['id'], job)
        self.assertEqual(self.submit(content=b'other').status_code, 409)
        self.assertTrue((self.service.root/job/'source.input').is_file())
        with patch.object(self.service, 'process', side_effect=self.fake_processing) as worker:
            self.assertTrue(self.service.work_once())
            self.assertFalse(self.service.work_once())
        worker.assert_called_once()
        self.assertFalse((self.service.root/job/'source.input').exists())
        self.assertEqual(self.submit().json['id'], job)
        self.service.recover()
        self.assertTrue((self.service.root/job/'preview.mp4').is_file())
        self.assertEqual(self.status(job).json['status'], 'complete')
        deleted = self.client.delete(self.base+'/media/'+job, headers=self.headers)
        self.assertEqual(deleted.json['status'], 'cancelled')
        self.assertFalse((self.service.root/job).exists())
        self.assertEqual(self.client.get(self.base+'/media/'+job+'/preview', headers=self.headers).status_code, 410)
        self.assertEqual(self.submit().json['status'], 'cancelled')

    def test_request_receipt_recovery_and_project_inventory(self):
        job = self.submit(request_id='saved-before-upload').json['id']
        receipt = self.base+'/media/request/saved-before-upload'
        self.assertEqual(self.client.get(receipt, headers=self.headers).json['id'], job)
        self.assertEqual(self.client.get(receipt).status_code, 401)
        self.assertEqual(self.client.get(receipt, headers={**self.headers, 'X-Vision-Project-Key': 'wrong'*10}).status_code, 403)
        self.assertEqual(self.client.get(self.base+'/media/request/never-accepted', headers=self.headers).status_code, 404)
        other = '/api/projects/project_video_other1'
        self.client.put(other, json={'project': {}, 'revision': 0}, headers=self.headers)
        self.assertEqual(self.client.get(other+'/media/request/saved-before-upload', headers=self.headers).status_code, 404)
        self.assertEqual(self.client.get(other+'/media', headers=self.headers).json['items'], [])
        self.assertEqual(self.client.get(other+'/media?cursor='+job, headers=self.headers).status_code, 404)
        self.assertEqual(self.client.get(self.base+'/media').status_code, 401)
        inventory = self.client.get(self.base+'/media', headers=self.headers).json
        self.assertEqual(inventory['items'][0]['id'], job)
        self.assertEqual(inventory['items'][0]['bytes'], 5)
        self.assertIsNone(inventory['nextCursor'])
        with patch.object(self.service, 'process', side_effect=self.fake_processing):
            self.service.work_once()
        self.assertEqual(self.client.get(receipt, headers=self.headers).json['status'], 'complete')
        self.assertTrue(self.client.get(receipt, headers=self.headers).json['resultReady'])
        self.assertFalse((self.service.root/job/'source.input').exists())
        # Receipts paginate deterministically, including jobs whose modules were removed.
        with server.connect_db() as db:
            for number in range(51):
                value = self.service.row(job)
                value.update(id=f'{number:024x}', request_id=f'older-{number}', created_at=value['created_at']-1)
                db.execute('INSERT INTO uploaded_media ('+','.join(value)+') VALUES ('+','.join('?' for _ in value)+')', list(value.values()))
        first = self.client.get(self.base+'/media', headers=self.headers).json
        second = self.client.get(self.base+'/media?cursor='+first['nextCursor'], headers=self.headers).json
        self.assertEqual(len(first['items']), 50)
        self.assertEqual(len(second['items']), 2)
        self.assertIsNone(second['nextCursor'])
        self.assertEqual(len({v['id'] for v in first['items']+second['items']}), 52)
        self.assertNotIn('thumbnail', first['items'][0])

    def test_limits_queue_reservations_and_no_leftover_files(self):
        self.assertEqual(self.submit(content=b'').status_code, 400)
        self.assertEqual(self.submit(name='../../config.json').status_code, 400)
        with patch.object(media, 'UPLOAD_LIMIT', 3):
            self.assertEqual(self.submit(content=b'four').status_code, 413)
        self.assertEqual(list(self.service.root.iterdir()), [])
        with patch.object(media.shutil, 'disk_usage', return_value=shutil._ntuple_diskusage(100, 100, 0)):
            self.assertEqual(self.submit().status_code, 507)
        with patch.object(media, 'PROJECT_LIMIT', 1):
            self.assertEqual(self.submit().status_code, 507)
        with patch.object(media, 'STORAGE_LIMIT', 1):
            self.assertEqual(self.submit().status_code, 507)
        for number in range(media.MAX_QUEUED):
            self.assertEqual(self.submit(request_id=f'video{number}').status_code, 202)
        self.assertEqual(self.submit(request_id='full').status_code, 429)
        self.assertEqual(self.submit(request_id='video0').status_code, 202)
        self.assertFalse(list(self.service.root.glob('incoming-*')))

    def test_restart_recovers_original_and_removes_partial_outputs(self):
        job = self.submit().json['id']
        directory = self.service.root/job
        (directory/'preview.mp4').write_bytes(b'partial')
        self.service.update(job, status='processing', attempts=1, progress=30)
        orphan = self.service.root/'incoming-orphan'
        orphan.mkdir()
        self.service.recover()
        self.assertTrue((directory/'source.input').exists())
        self.assertFalse((directory/'preview.mp4').exists())
        self.assertFalse(orphan.exists())
        self.assertEqual(self.status(job).json['status'], 'queued')
        self.service.update(job, status='processing', attempts=3)
        self.service.recover()
        self.assertEqual(self.status(job).json['status'], 'error')
        self.assertFalse(directory.exists())

    def test_cancel_stop_error_cleanup_and_shared_cpu_slot(self):
        job = self.submit().json['id']
        with media.MEDIA_LOCK:
            self.assertFalse(self.service.work_once())
        self.assertEqual(self.status(job).json['status'], 'queued')
        with patch.object(self.service, 'process', side_effect=media.MediaStopped):
            self.assertTrue(self.service.work_once())
        self.assertEqual(self.status(job).json['status'], 'queued')
        self.assertTrue((self.service.root/job/'source.input').exists())
        with self.assertLogs(server.app.logger, level='ERROR'), patch.object(self.service, 'process', side_effect=RuntimeError('/private/path token-secret')), patch.object(server.requests, 'request') as network:
            self.assertTrue(self.service.work_once())
        network.assert_not_called()
        self.assertEqual(self.status(job).json['status'], 'error')
        self.assertNotIn('private', self.status(job).json['error'])
        self.assertFalse((self.service.root/job).exists())
        self.assertFalse(self.service.work_once())
        second = self.submit(request_id='cancel').json['id']
        self.service.update(second, cancel_requested=1)
        self.service.recover()
        self.assertEqual(self.status(second).json['status'], 'cancelled')
        self.assertFalse((self.service.root/second).exists())

    def test_cancellation_during_final_write_wins_completion(self):
        job = self.submit().json['id']
        def finish_then_cancel(value):
            self.fake_processing(value)
            response = self.client.delete(self.base+'/media/'+job, headers=self.headers)
            self.assertEqual(response.status_code, 200)
        with patch.object(self.service, 'process', side_effect=finish_then_cancel):
            self.assertTrue(self.service.work_once())
        self.assertEqual(self.status(job).json['status'], 'cancelled')
        self.assertFalse((self.service.root/job).exists())
        self.assertEqual(self.client.get(self.base+'/media/'+job+'/result', headers=self.headers).status_code, 410)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg is required for real media test')
    def test_real_video_and_silent_video_create_complete_small_previews(self):
        server.app.config['FFMPEG'] = shutil.which('ffmpeg')
        for audio in (True, False):
            source = self.root/f'fixture-{audio}.mp4'
            command = [shutil.which('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
                       '-i', 'testsrc=size=640x360:rate=30']
            if audio:
                command += ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000']
            command += ['-t', '2', '-c:v', 'libx264', '-threads', '1', '-pix_fmt', 'yuv420p']
            if audio:
                command += ['-c:a', 'aac']
            subprocess.run(command+[str(source)], check=True, timeout=30, capture_output=True)
            accepted = self.submit(content=source.read_bytes(), request_id=f'real-{audio}')
            self.assertEqual(accepted.status_code, 202, accepted.json)
            job = accepted.json['id']
            self.assertTrue(self.service.work_once())
            status = self.status(job).json
            self.assertEqual(status['status'], 'complete', status)
            self.assertTrue(status['thumbnail'].startswith('data:image/jpeg;base64,'))
            response = self.client.get(self.base+'/media/'+job+'/result', headers=self.headers)
            result = response.json
            response.close()
            self.assertGreater(len(result['frames']), 0)
            self.assertEqual(result['frames'][0]['timestamp'], 0)
            self.assertEqual(max(result['preview']['width'], result['preview']['height']), 480)
            self.assertEqual(len(result['audioSections']), int(audio))
            if audio:
                section = result['audioSections'][0]
                self.assertEqual(section['start'], 0)
                self.assertAlmostEqual(section['end'], result['duration'])
                response = self.client.get(section['url'], headers=self.headers)
                self.assertEqual(response.status_code, 200)
                self.assertLess(len(response.data), media.AUDIO_LIMIT)
                self.assertEqual(response.mimetype, 'audio/mpeg')
                response.close()
            self.assertFalse((self.service.root/job/'source.input').exists())
            info = json.loads(subprocess.run([shutil.which('ffprobe'), '-v', 'error', '-show_streams', '-of', 'json',
                                             str(self.service.root/job/'preview.mp4')], capture_output=True, check=True).stdout)
            video_stream = next(s for s in info['streams'] if s['codec_type'] == 'video')
            self.assertEqual(video_stream['r_frame_rate'], '15/1')
            self.assertTrue(all(s.get('channels') == 1 for s in info['streams'] if s['codec_type'] == 'audio'))

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg is required for real media test')
    def test_delayed_audio_preserves_video_timestamps_and_trailing_silence(self):
        server.app.config['FFMPEG'] = shutil.which('ffmpeg')
        source = self.root/'delayed.mp4'
        subprocess.run([shutil.which('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-y',
                        '-f', 'lavfi', '-i', 'color=c=black:s=160x90:r=10:d=6', '-itsoffset', '2',
                        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000:duration=2',
                        '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'libx264', '-threads', '1',
                        '-c:a', 'aac', str(source)], check=True, capture_output=True, timeout=30)
        job = self.submit(content=source.read_bytes()).json['id']
        # Smaller test chunks exercise exactly the same timeline slicing across
        # a section boundary without creating a 15-minute fixture.
        with patch.object(media, 'AUDIO_SECTION_SECONDS', 3):
            self.assertTrue(self.service.work_once())
        self.assertEqual(self.status(job).json['status'], 'complete', self.status(job).json)
        raw = b''.join(subprocess.run([shutil.which('ffmpeg'), '-v', 'error', '-i',
                                      str(self.service.root/job/f'audio-{index:03d}.mp3'), '-f', 's16le', '-ac', '1', '-ar', '16000', '-'],
                                     capture_output=True, check=True, timeout=30).stdout for index in range(2))
        samples = array.array('h', raw)
        self.assertAlmostEqual(len(samples)/16000, 6, delta=.1)
        def rms(start, end):
            part = samples[round(start*16000):round(end*16000)]
            return math.sqrt(sum(value*value for value in part)/len(part))
        self.assertLess(rms(.2, 1.8), 20)  # original leading two-second silence is kept
        self.assertGreater(rms(2.2, 3.8), 500)
        self.assertLess(rms(4.3, 5.8), 20)  # video continues after the original audio track
        result = json.loads((self.service.root/job/'result.json').read_text())
        self.assertEqual(result['audioSections'][0]['start'], 0)
        self.assertEqual(result['audioSections'][0]['end'], 3)
        self.assertEqual(result['audioSections'][1]['start'], 3)
        self.assertAlmostEqual(result['audioSections'][1]['end'], 6, delta=.1)


if __name__ == '__main__':
    unittest.main()
