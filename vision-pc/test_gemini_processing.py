"""Gemini queue/security/media contracts with no cloud calls or model downloads."""
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import gemini_processing as gemini
from gemini_access import GeminiAccessDenied, public_id
import server
import test_transcription as existing_local
from transcription import atomic_json, combine_transcription


def response(data, status=200):
    result = Mock(status_code=status)
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.iter_content.return_value = [json.dumps(data).encode()]
    return result


def sound_response(present=True, label='Warm piano music plays softly.'):
    return {'status': 'completed', 'steps': [{'type': 'model_output', 'content': [{'type': 'text', 'text': json.dumps(
        {'present': present, 'description': label if present else '', 'start': 1 if present else 0, 'end': 3 if present else 0})}]}],
        'usage': {'total_input_tokens': 100, 'total_output_tokens': 20, 'total_thought_tokens': 15, 'total_tokens': 135}}


class GeminiPureTests(unittest.TestCase):
    def test_transcribe_word_annotations_keep_offsets_and_reject_invented_timestamps(self):
        payload = {'steps': [{'content': [{'type': 'text', 'text': 'Hello there.', 'annotations': [
            {'type': 'word_info', 'text': 'Hello', 'start_offset': '1.250s', 'end_offset': '1.500s'},
            {'type': 'word_info', 'text': 'there.', 'start_offset': '1.600s', 'end_offset': '2.750s'}]}]}]}
        self.assertEqual(gemini.speech_segments(payload, 600, 30), [{'start': 601.25, 'end': 602.75, 'text': 'Hello there.'}])
        with self.assertRaises(gemini.GeminiProcessingError):
            gemini.speech_segments({'steps': [{'content': [{'type': 'text', 'text': 'Untimed words.'}]}]}, 0, 30)
        with self.assertRaises(gemini.GeminiProcessingError):
            gemini.speech_segments(payload, 0, 1)

    def test_sound_grouping_caps_deduplicates_and_samples_long_continuous_music(self):
        sections = [{'start': 0, 'end': 900}, {'start': 900, 'end': 1800}, {'start': 1800, 'end': 2700}]
        raw = [{'start': 0, 'end': 900, 'label': 'Music', 'score': .9},
               {'start': 20, 'end': 200, 'label': 'Piano', 'score': .8},
               {'start': 900, 'end': 1800, 'label': 'Music', 'score': .9},
               {'start': 1800, 'end': 2700, 'label': 'Music', 'score': .9},
               {'start': 5, 'end': 7, 'label': 'Applause', 'score': .8},
               {'start': 6, 'end': 8, 'label': 'Clapping', 'score': .9}]
        groups = gemini.continuous_groups(gemini.merge_events(raw, sections), sections)
        self.assertEqual(len(groups), 2)
        music = next(event for event in groups if event['family'] == 'music')
        clips = gemini.representative_clips(music, sections)
        self.assertEqual(len(clips), 2, 'A long event crossing uploads still uses only two clips')
        self.assertTrue(all(0 < end-start <= 20 for _, start, end in clips))
        applause = next(event for event in groups if event['family'] == 'applause')
        self.assertEqual((applause['start'], applause['end']), (5, 8))
        self.assertEqual(gemini.representative_clips(applause, sections), [(0, 3.5, 9.5)])
        gap = [{'start': 0, 'end': 10}, {'start': 20, 'end': 30}]
        separated = gemini.merge_events([{'start': 8, 'end': 10, 'label': 'Music', 'score': .9},
                                         {'start': 20, 'end': 22, 'label': 'Music', 'score': .9}], gap)
        self.assertEqual(len(gemini.continuous_groups(separated, gap)), 2)

    def test_gemini_only_uses_double_star_sound_captions(self):
        speech = {'text': 'Hi.', 'sections': [{'start': 0, 'end': 10, 'text': 'Hi.'}],
                  'speechSegments': [{'start': 1, 'end': 3, 'text': 'Hi.'}]}
        event = {'start': 2, 'end': 4, 'score': .9, 'label': 'Gentle acoustic guitar music.', 'enhanced': True}
        result = combine_transcription(speech, {'status': 'completed', 'soundEvents': [event]})
        self.assertIn('**Gentle acoustic guitar music.**', result['combinedSrt'])
        self.assertEqual(result['speechText'], 'Hi.')
        self.assertIn('00:00:02,000 --> 00:00:03,000', result['combinedSrt'])
        self.assertEqual(gemini.sound_description(sound_response(False), 0, 5), {'present': False})


class GeminiQueueTests(unittest.TestCase):
    def setUp(self):
        existing_local.TranscriptionTests.setUp(self)
        self.identity = SimpleNamespace(project_id='visionboard-api', verify=lambda token: {
            'sub': token, 'name': token.title(), 'email': token+'@example.com', 'auth_time': 1})
        server.app.config['FIREBASE_IDENTITY'] = self.identity
        self.headers = {'Authorization': 'Bearer alice', 'Origin': 'https://jrdn-r.github.io'}
        self.base = '/api/projects/project_gemini_01'
        self.client.put(self.base, json={'project': {'nodes': []}, 'revision': 0}, headers=self.headers)
        self.body['provider'] = 'gemini'
        self.access = server.app.config['GEMINI_ACCESS']
        self.credentials = Mock()
        self.credentials.get.return_value = 'private-server-key'
        server.app.config['GEMINI_CREDENTIALS'] = self.credentials

    tearDown = existing_local.TranscriptionTests.tearDown
    submit = existing_local.TranscriptionTests.submit
    status = existing_local.TranscriptionTests.status

    def approved_job(self, include_sounds=False):
        self.access.request_access('firebase:alice')
        self.access.decide(public_id('firebase:alice'), 'approved', 'firebase:owner')
        with patch.object(self.service, 'sound_capability', return_value={'ready': True}):
            return self.submit({**self.body, 'includeSoundEvents': include_sounds}).json['id']

    def processor(self, job):
        value = self.service.row(job)
        manifest = json.loads((self.service.root / job / 'manifest.json').read_text())
        return gemini.GeminiProcessor(self.service, value, manifest)

    def test_pending_submission_makes_zero_calls_and_approval_releases_same_receipt(self):
        with patch.object(gemini.requests, 'post') as network, patch.object(self.service, 'run_gemini') as worker:
            submitted = self.submit()
            self.assertEqual(submitted.status_code, 202, submitted.json)
            job = submitted.json['id']
            self.assertEqual(submitted.json['status'], 'approval_waiting')
            self.assertEqual(submitted.json['phase'], 'Waiting for Gemini approval')
            self.assertEqual(self.access.status('firebase:alice'), 'pending')
            self.assertFalse(self.service.work_once())
            worker.assert_not_called()
            network.assert_not_called()
        self.assertEqual(self.submit().json['id'], job)
        released = self.access.decide(public_id('firebase:alice'), 'approved', 'firebase:owner')
        self.assertEqual(released['releasedJobs'], 1)
        self.assertEqual(self.status(job).json['status'], 'queued')
        self.service.settings['enabled'] = False  # Gemini does not depend on Whisper readiness.
        with patch.object(self.service, 'run_gemini', return_value={'text': 'Hi', 'sections': []}) as worker:
            self.assertTrue(self.service.work_once())
        worker.assert_called_once()
        self.assertEqual(self.status(job).json['status'], 'complete')
        self.assertEqual(self.submit({**self.body, 'clientRequestId': 'future'}).json['status'], 'queued')
        serialized = json.dumps(self.status(job).json)
        for private in ('private-server-key', 'usage', 'inputTokens', 'estimatedCost'):
            self.assertNotIn(private, serialized)

    def test_denial_revocation_and_account_isolation_fail_closed(self):
        job = self.submit().json['id']
        self.access.decide(public_id('firebase:alice'), 'denied', 'firebase:owner')
        self.assertEqual(self.status(job).json['phase'], 'Gemini access denied')
        other = {**self.headers, 'Authorization': 'Bearer bob'}
        self.assertEqual(self.status(job, headers=other).status_code, 404)
        self.assertEqual(self.submit({**self.body, 'clientRequestId': 'denied-new'}).json['status'], 'approval_waiting')
        self.access.decide(public_id('firebase:alice'), 'approved', 'firebase:owner')
        self.access.decide(public_id('firebase:alice'), 'revoked', 'firebase:owner')
        with patch.object(gemini.requests, 'post') as network:
            self.assertFalse(self.service.work_once())
            with self.assertRaises(GeminiAccessDenied):
                self.processor(job).request(gemini.SOUND_MODEL, 'sound', b'audio', {}, lambda data: data, 'test-key',
                                           event_start=600, event_end=605, clip_duration=5)
            network.assert_not_called()
        self.credentials.get.assert_not_called()

    def test_transport_logs_owner_usage_and_checkpoints_avoid_repeat_calls(self):
        job = self.approved_job()
        processor = self.processor(job)
        def produce(key):
            return processor.request(gemini.SOUND_MODEL, 'sound', b'audio',
                {'thinking_level': 'low', 'max_output_tokens': 512},
                lambda data: gemini.sound_description(data, 600, 605), key,
                event_start=601, event_end=603, clip_duration=5,
                prompt='Describe only this sound.', response_format={'type': 'text', 'mime_type': 'application/json'})
        with patch.object(gemini.requests, 'post', return_value=response(sound_response())) as network:
            first = processor.checkpoint('event:1', produce)
            self.assertEqual(processor.checkpoint('event:1', produce), first)
            self.assertEqual(network.call_count, 1)
            sent = network.call_args.kwargs
            self.assertEqual(sent['json']['model'], gemini.SOUND_MODEL)
            self.assertEqual(sent['json']['generation_config'], {'thinking_level': 'low', 'max_output_tokens': 512})
            self.assertFalse(sent['allow_redirects'])
            self.assertEqual(sent['headers']['x-goog-api-key'], 'private-server-key')
        with server.connect_db() as db:
            records = [dict(row) for row in db.execute('SELECT * FROM gemini_usage WHERE job_id=?', (job,))]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['status'], 'succeeded')
        self.assertEqual(records[0]['uid'], 'firebase:alice')
        self.assertEqual((records[0]['event_start'], records[0]['event_end'], records[0]['clip_duration']), (601, 603, 5))
        self.assertEqual(json.loads(records[0]['raw_usage'])['total_thought_tokens'], 15)
        for path in (self.service.root / job).glob('*.json'):
            self.assertNotIn('private-server-key', path.read_text())

    def test_speech_uses_dedicated_timestamp_model_and_checkpointed_original_audio_sections(self):
        job = self.approved_job()
        processor = self.processor(job)
        section = {**processor.manifest['sections'][0], 'duration': 30}
        spoken = {'status': 'completed', 'steps': [{'content': [{'type': 'text', 'text': 'Hello.', 'annotations': [
            {'type': 'word_info', 'text': 'Hello.', 'start_offset': '1.250s', 'end_offset': '2.750s'}]}]}],
            'usage': {'total_input_tokens': 120, 'total_output_tokens': 10, 'total_tokens': 130}}
        with patch.object(processor, 'extract', return_value=b'original-section-audio') as extract, \
                patch.object(gemini.requests, 'post', return_value=response(spoken)) as network:
            result = processor.transcribe([section])
            self.assertEqual(processor.transcribe([section]), result)
        self.assertEqual(network.call_count, 1)
        extract.assert_called_once_with(section, 600.0, 630.0, 'gemini-speech-working.mp3')
        sent = network.call_args.kwargs['json']
        self.assertEqual(sent['model'], 'gemini-3.5-transcribe')
        self.assertEqual(sent['generation_config'], {'transcription_config': {'mode': {
            'type': 'verbatim', 'timestamp_granularities': ['word']}}})
        self.assertEqual(result['speechSegments'], [{'start': 601.25, 'end': 602.75, 'text': 'Hello.'}])
        self.assertIn('00:10:01,250 --> 00:10:02,750', result['combinedSrt'])
        self.assertNotIn('usage', result)

    def test_receipt_without_checkpoint_never_replays_billable_request(self):
        job = self.approved_job()
        processor = self.processor(job)
        def call():
            return processor.request(gemini.SOUND_MODEL, 'sound', b'audio', {}, lambda data: {'present': False},
                                     'fixed-request-key', event_start=600, event_end=605, clip_duration=5)
        with patch.object(gemini.requests, 'post', return_value=response(sound_response(False))) as network:
            self.assertEqual(call(), {'present': False})
            with self.assertRaisesRegex(gemini.GeminiProcessingError, 'earlier or unavailable usage receipt'):
                call()
            self.assertEqual(network.call_count, 1)

    def test_revoke_immediately_before_transport_and_failed_response_never_leaks(self):
        job = self.approved_job()
        processor = self.processor(job)
        real_begin = processor.usage.begin_request
        def revoke(*args, **kwargs):
            request_id = real_begin(*args, **kwargs)
            self.access.decide(public_id('firebase:alice'), 'revoked', 'firebase:owner')
            return request_id
        with patch.object(processor.usage, 'begin_request', side_effect=revoke), patch.object(gemini.requests, 'post') as network:
            with self.assertRaises(GeminiAccessDenied):
                processor.request(gemini.SOUND_MODEL, 'sound', b'audio', {}, lambda data: data, 'revoke',
                                  event_start=600, event_end=605, clip_duration=5)
            network.assert_not_called()
        self.access.decide(public_id('firebase:alice'), 'approved', 'firebase:owner')
        with patch.object(gemini.requests, 'post', return_value=response(sound_response(False))) as resumed:
            processor.request(gemini.SOUND_MODEL, 'sound', b'audio', {}, lambda data: {'present': False}, 'revoke',
                              event_start=600, event_end=605, clip_duration=5)
        self.assertEqual(resumed.call_count, 1, 'Reapproval safely resumes an event that was never submitted')
        with patch.object(gemini.requests, 'post', return_value=response({'secret': 'do-not-display'}, status=400)):
            with self.assertRaises(gemini.GeminiProcessingError) as failed:
                processor.request(gemini.SOUND_MODEL, 'sound', b'audio', {}, lambda data: data, 'failed',
                                  event_start=600, event_end=605, clip_duration=5)
        self.assertNotIn('do-not-display', str(failed.exception))

    def test_stage_one_parallel_stage_two_waits_and_false_positives_are_discarded(self):
        job = self.approved_job(include_sounds=True)
        processor = self.processor(job)
        speech_started, detector_finished = threading.Event(), threading.Event()
        speech = {'text': 'Hi', 'sections': [{'start': 600, 'end': 630, 'text': 'Hi'}],
                  'speechSegments': [{'start': 601, 'end': 602, 'text': 'Hi'}]}
        sounds = {'status': 'completed', 'soundEvents': [{'start': 602, 'end': 604, 'label': 'Music', 'score': .9}]}
        def transcribe(_sections):
            speech_started.set()
            self.assertTrue(detector_finished.wait(2), 'Stage 1 must allow both workers to progress')
            return speech
        def detect(*_args, **_kwargs):
            self.assertTrue(speech_started.wait(2))
            detector_finished.set()
            return sounds
        def enhance(found, _sections):
            self.assertTrue(detector_finished.is_set())
            self.assertEqual(found, sounds)
            return {'status': 'completed', 'soundEvents': []}
        with patch.object(processor, 'probe_sections', return_value=processor.manifest['sections']), \
                patch.object(processor, 'transcribe', side_effect=transcribe), \
                patch.object(self.service, 'run_sound_events', side_effect=detect), \
                patch.object(processor, 'enhance', side_effect=enhance):
            result = processor.run()
        self.assertEqual(result['soundEvents'], [])
        self.assertEqual(result['speechText'], 'Hi')
        self.assertNotIn('Music', result['text'])

    def test_speech_failure_stops_parallel_local_detector_without_waiting_for_inference(self):
        job = self.approved_job(include_sounds=True)
        processor = self.processor(job)
        stopped = threading.Event()
        def detect(*_args, abort=None):
            self.assertIsNotNone(abort)
            if abort.wait(2):
                stopped.set()
            return None
        with patch.object(processor, 'probe_sections', return_value=processor.manifest['sections']), \
                patch.object(processor, 'transcribe', side_effect=gemini.GeminiProcessingError('Gemini could not finish.')), \
                patch.object(self.service, 'run_sound_events', side_effect=detect):
            with self.assertRaises(gemini.GeminiProcessingError):
                processor.run()
        self.assertTrue(stopped.is_set())

    def test_only_bounded_original_audio_clips_get_sound_analysis_and_rejections_cache(self):
        job = self.approved_job(include_sounds=True)
        processor = self.processor(job)
        sections = [{**processor.manifest['sections'][0], 'start': 0, 'end': 900}]
        local = {'status': 'completed', 'soundEvents': [{'start': 0, 'end': 900, 'label': 'Music', 'score': .9},
                 {'start': 10, 'end': 20, 'label': 'Piano', 'score': .8},
                 {'start': 40, 'end': 41, 'label': 'Clapping', 'score': .9}]}
        calls = []
        def extract(section, start, end, filename):
            self.assertEqual(section['file'], 'audio-000.mp3')
            self.assertLessEqual(end-start, 20)
            calls.append((start, end))
            return b'bounded-original-audio'
        def fake_request(model, kind, audio, generation, parser, request_key, **kwargs):
            self.assertEqual(generation, {'thinking_level': 'low', 'max_output_tokens': 512})
            self.assertEqual(kind, 'sound')
            self.assertEqual(audio, b'bounded-original-audio')
            if kwargs['event_start'] == 40:
                return {'present': False}
            return {'present': True, 'label': 'Soft warm piano music.', 'start': 0, 'end': 20}
        with patch.object(processor, 'extract', side_effect=extract), patch.object(processor, 'request', side_effect=fake_request) as network:
            first = processor.enhance(local, sections)
            again = processor.enhance(local, sections)
        self.assertEqual(network.call_count, 3, 'Two music representatives plus one rejected event; zero repeat calls')
        self.assertEqual(first, again)
        self.assertEqual(len(first['soundEvents']), 1)
        self.assertEqual((first['soundEvents'][0]['start'], first['soundEvents'][0]['end']), (0, 900))


if __name__ == '__main__':
    unittest.main()
