"""Offline import integration: durable queues, ownership, history and real codecs."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import server
import test_vortex
from vortex_imports import prepare_source
from vortex_worker import export_media, format_selector, probe_file


class ImportTests(unittest.TestCase):
    setUpClass = classmethod(test_vortex.VortexAccountTests.setUpClass.__func__)
    headers = test_vortex.VortexAccountTests.headers

    def setUp(self):
        test_vortex.VortexAccountTests.setUp(self)
        self.project = 'vortex_import_project_01'
        response = self.client.put('/api/projects/' + self.project,
            json={'project': {'nodes': []}, 'revision': 0}, headers=self.alice)
        self.assertEqual(response.status_code, 200, response.json)
        self.capability = patch.object(server.vortex, 'capability', return_value={'ready': True, 'engines': {'yt-dlp': True}})
        self.capability.start()
        self.asr = patch.object(server.transcriptions, 'capability', return_value={'ready': True})
        self.asr.start()

    def tearDown(self):
        self.capability.stop()
        self.asr.stop()
        test_vortex.VortexAccountTests.tearDown(self)

    def submit(self, headers=None, **changes):
        body = dict(url='https://www.youtube.com/watch?v=abcdefghijk', projectId=self.project,
                    clientRequestId='vortex-import-test', provider='local')
        body.update(changes)
        return self.client.post('/api/vortex/import', json=body, headers=headers or self.alice)

    def advance(self, job_id):
        with server.connect_db() as db:
            db.execute('UPDATE jobs SET retry_at=0 WHERE id=?', (job_id,))
        self.assertTrue(server.process_next_job())
        return server.get_job(job_id)

    def low_job(self):
        with server.connect_db() as db:
            return dict(db.execute("SELECT * FROM vortex_jobs WHERE purpose='vision'").fetchone())

    def finish_low(self, audio=False):
        low = self.low_job()
        directory = server.vortex.root / low['id']
        directory.mkdir(exist_ok=True)
        name = 'export.m4a' if audio else 'export.mp4'
        (directory / name).write_bytes(b'verified media fixture')
        with server.connect_db() as db:
            db.execute("UPDATE vortex_jobs SET status='processing' WHERE id=?", (low['id'],))
        server.vortex._event(low['id'], {'media': {'title': 'Fixture', 'mediaType': 'audio' if audio else 'video'}})
        self.assertTrue(server.vortex._finish(low['id'], {'filename': name}))
        return low

    def test_owner_only_and_idempotent_across_restarts(self):
        first = self.submit()
        self.assertEqual(first.status_code, 202, first.json)
        self.assertEqual(self.submit().json['id'], first.json['id'])
        self.assertEqual(self.submit(url='https://example.com/audio.mp3').status_code, 409)
        self.assertEqual(self.submit(headers=self.bob).status_code, 404)
        self.assertNotEqual(self.submit(headers=self.legacy).status_code, 202)
        self.assertEqual(self.client.get('/api/jobs/' + first.json['id'], headers=self.bob).status_code, 404)
        row = self.advance(first.json['id'])
        self.assertEqual(row['status'], 'queued')
        low = self.low_job()
        server.recover_jobs()
        self.advance(first.json['id'])
        self.assertEqual(self.low_job()['id'], low['id'])
        self.assertEqual(self.client.get('/api/vortex/jobs', headers=self.alice).json['jobs'], [])

    def test_history_is_separate_balanced_default_and_queued_only_once(self):
        job = self.submit().json['id']
        self.advance(job)
        low = self.finish_low()
        server.vortex.queue_vision_history()
        history = self.client.get('/api/vortex/jobs?kind=download', headers=self.alice).json['jobs']
        self.assertEqual(len(history), 1)
        self.assertNotEqual(history[0]['id'], low['id'])
        self.assertEqual((history[0]['quality'], history[0]['videoFormat'], history[0]['downloadMode']), ('balanced', 'mp4', 'video'))
        self.assertEqual(self.client.get('/api/vortex/jobs', headers=self.bob).json['jobs'], [])
        self.assertEqual(self.client.get('/api/vortex/jobs/' + low['id'], headers=self.bob).status_code, 404)

    def test_background_transcription_and_audio_fallback_need_no_browser(self):
        job = self.submit(url='https://music.youtube.com/watch?v=abcdefghijk').json['id']
        self.advance(job)
        low = self.finish_low(audio=True)
        prepared = dict(title='Track', mediaType='audio', duration=2, snapshotInterval=None, frames=[], hasAudio=True)
        sections = [dict(start=0, end=2, mimeType='audio/mpeg', audioData='data:audio/mpeg;base64,SUQzYXVkaW8=')]
        with patch('vortex_imports.prepare_source', return_value=(prepared, sections)) as extract:
            row = self.advance(job)
            self.assertEqual(row['status'], 'queued')
            row = self.advance(job)
            receipt = json.loads(Path(row['result_path']).read_text())['transcriptionId']
            self.assertEqual(server.transcriptions.row(receipt)['requester_uid'], 'firebase:alice')
            self.assertFalse((server.vortex.root / low['id']).exists(), 'temporary processing media must be removed')
            server.recover_jobs()
            self.advance(job)
            self.assertEqual(extract.call_count, 1, 'restart must reuse prepared frames and the durable ASR receipt')
        result = {'text': '[00:00:00.000] Hello', 'sections': [{'start': 0, 'end': 2, 'text': 'Hello'}]}
        with server.connect_db() as db:
            db.execute("UPDATE local_transcriptions SET status='complete',result_json=? WHERE id=?", (json.dumps(result), receipt))
        row = self.advance(job)
        self.assertEqual(row['status'], 'complete')
        received = self.client.get('/api/jobs/' + job + '/result', headers=self.alice).json
        self.assertEqual(received['transcription'], result)
        self.assertEqual(received['mediaType'], 'audio')
        self.assertNotIn('audioData', json.dumps(received))
        history = self.client.get('/api/vortex/jobs', headers=self.alice).json['jobs']
        self.assertEqual((history[0]['downloadMode'], history[0]['audioFormat']), ('audio', 'm4a'))
        self.client.delete('/api/jobs/' + job, headers=self.alice)
        self.assertEqual(len(self.client.get('/api/vortex/jobs', headers=self.alice).json['jobs']), 1)

    def test_history_capacity_failure_retries_after_processing_copy_removed(self):
        job = self.submit().json['id']
        self.advance(job)
        with patch.object(server.vortex, 'enqueue', side_effect=server.APIError('Queue full', 429)):
            self.finish_low()
        low = self.low_job()
        self.assertIsNone(low['history_id'])
        with server.connect_db() as db:
            db.execute("UPDATE vortex_jobs SET status='expired',filename=NULL WHERE id=?", (low['id'],))
        server.vortex._remove_terminal(low['id'])
        server.vortex.queue_vision_history()
        self.assertIsNotNone(self.low_job()['history_id'])

    def test_vortex_video_enters_normal_frame_collection_and_analysis_queue(self):
        from PIL import Image
        from frame_analysis import image_metadata
        import io
        processing = {'screenshotDetail':'maximum','describeFrames':True,
                      'frameDescriptionEngine':'auto'}
        job = self.submit(processing=processing).json['id']
        self.advance(job)
        self.finish_low()
        captured = []
        def prepared(_source, _metadata, _ffmpeg, _temp, _update, **kwargs):
            captured.append(kwargs)
            store = kwargs['frame_store']
            self.assertIs(store, server.frame_analysis)
            self.assertEqual(kwargs['collection_id'],job)
            self.assertEqual(kwargs['processing'],processing)
            store.create(self.project,job)
            store.set_source_title(job,self.project,'Vortex Hardware Demonstration')
            picture = Image.new('RGB',(320,180),'navy')
            stream = io.BytesIO();picture.save(stream,format='JPEG')
            frame = store.add(job,0,0,stream.getvalue(),image_metadata(picture))
            return (dict(title='Vortex Hardware Demonstration',mediaType='video',
                         duration=4,frameCollection=job,frames=[frame],
                         thumbnail='data:image/jpeg;base64,' + __import__('base64').b64encode(stream.getvalue()).decode(),
                         snapshotInterval=.25,hasAudio=False),[])
        with patch('vortex_imports.prepare_source',side_effect=prepared):
            self.advance(job)
            self.advance(job)
            row = self.advance(job)
        self.assertEqual(row['status'],'complete')
        self.assertEqual(len(captured),1)
        saved = self.client.get('/api/jobs/'+job+'/result',headers=self.alice).json
        self.assertEqual(saved['frameCollection'],job)
        self.assertEqual(saved['frames'][0]['frameRef']['collectionId'],job)
        self.assertNotIn('data',saved['frames'][0])
        frame_row = server.frame_analysis.row(job)
        self.assertEqual(frame_row['caption_engine'],'luna')
        self.assertEqual(frame_row['fallback_enabled'],1)
        self.assertEqual(frame_row['analysis_step'],1)
        self.assertEqual(frame_row['status'],'queued')
        title,cues=server.frame_analysis.caption_material(frame_row)
        self.assertEqual(title,'Vortex Hardware Demonstration')
        self.assertEqual(cues,[])
        frame_read = self.client.get('/api/projects/'+self.project+'/frames/'+job+'/0',headers=self.alice)
        self.assertEqual(frame_read.status_code,200)
        self.assertEqual(frame_read.mimetype,'image/jpeg')
        self.assertEqual(self.client.get('/api/projects/'+self.project+'/frames/'+job+'/0',headers=self.bob).status_code,404)

    def test_deleted_project_stops_before_download(self):
        job = self.submit().json['id']
        with server.connect_db() as db:
            db.execute('DELETE FROM projects WHERE id=?', (self.project,))
        self.assertEqual(self.advance(job)['status'], 'error')
        with server.connect_db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM vortex_jobs').fetchone()[0], 0)

    def test_public_request_cannot_select_internal_profile(self):
        response = self.client.post('/api/vortex/jobs', json=dict(input='https://example.com/video.mp4',
            kind='download', quality='small', purpose='vision', processingProfile='vision'), headers=self.alice)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(server.vortex.row(response.json['id'])['purpose'], '')


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class CodecTests(unittest.TestCase):
    def test_real_lightweight_video_and_audio(self):
        ffmpeg = shutil.which('ffmpeg')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for audio in (False, True):
                source = root / ('source.m4a' if audio else 'source.mp4')
                command = [ffmpeg, '-loglevel', 'error', '-y']
                if not audio:
                    command += ['-f', 'lavfi', '-i', 'color=c=blue:s=640x480:r=10']
                command += ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100', '-t', '2']
                if not audio:
                    command += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p']
                command += ['-c:a', 'aac', '-ac', '2', str(source)]
                subprocess.run(command, check=True, capture_output=True)
                media = {'mediaType': 'audio' if audio else 'video'}
                with patch('vortex_worker.emit'):
                    output = export_media(source, media, dict(directory=temp, ffmpeg=ffmpeg, processingProfile='vision', quality='small'))
                details = probe_file(output, ffmpeg, root)
                stream = next(s for s in details['streams'] if s['codec_type'] == 'audio')
                self.assertEqual(stream['channels'], 1)
                self.assertEqual(int(stream['sample_rate']), 16000)
                result, sections = prepare_source(output, {'title': 'Test'}, ffmpeg, temp, lambda **_: None)
                self.assertEqual(result['mediaType'], 'audio' if audio else 'video')
                self.assertEqual(bool(result['frames']), not audio)
                self.assertEqual(len(sections), 1)
                self.assertEqual(sections[0]['start'], 0)
                self.assertAlmostEqual(sections[0]['end'], 2, delta=.1)
                # The real Vortex analysis proxy uses regular video presets
                # and preserves full image refs instead of inline JPEG data.
                class Store:
                    created = False
                    title = None
                    def create(self, project, collection):
                        self.created = True
                        assert (project,collection)==('vortex_codec_project','f'*24)
                    def set_source_title(self, collection, project, title):
                        self.title = title
                    def add(self, collection, index, when, jpeg, metadata):
                        assert len(jpeg) <= 512*1024
                        assert len(metadata['scenePixels'])==192
                        return dict(name=f'frame-{index+1:04}.jpg',timestamp=when,
                                    frameRef=dict(collectionId=collection,index=index,projectId='vortex_codec_project'),
                                    size=len(jpeg))
                store = Store()
                processed, _ = prepare_source(
                    output, {'title':'Vortex codec test'}, ffmpeg, temp, lambda **_:None,
                    processing={'screenshotDetail':'maximum','describeFrames':True,'frameDescriptionEngine':'auto'},
                    frame_store=store, project_id='vortex_codec_project',collection_id='f'*24)
                if audio:
                    self.assertFalse(store.created)
                    self.assertNotIn('frameCollection',processed)
                    self.assertEqual(processed['frames'],[])
                else:
                    self.assertTrue(store.created)
                    self.assertEqual(store.title,'Vortex codec test')
                    self.assertEqual(processed['frameCollection'],'f'*24)
                    self.assertEqual(len(processed['frames']),8)
                    self.assertTrue(all('frameRef' in f and 'data' not in f for f in processed['frames']))
                    self.assertTrue(processed['thumbnail'].startswith('data:image/jpeg;base64,'))
                output.unlink()
        self.assertIn('height>=320', format_selector('small', processing=True))
        self.assertEqual(format_selector(audio=True, processing=True), 'worstaudio/worst[acodec!=none]')


if __name__ == '__main__':
    unittest.main()
