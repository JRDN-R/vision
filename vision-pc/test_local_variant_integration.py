"""Real authenticated queue/settings tests; inference is a named protocol fixture."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_local_inference as existing
import local_runtime
import local_variants
from setup_local_variant import atomic_json


class VariantIntegrationTests(unittest.TestCase):
    setUpClass = classmethod(existing.LocalInferenceTests.setUpClass.__func__)
    token = existing.LocalInferenceTests.token
    headers = existing.LocalInferenceTests.headers
    new = existing.LocalInferenceTests.new
    send = existing.LocalInferenceTests.send
    tearDown = existing.LocalInferenceTests.tearDown

    def setUp(self):
        existing.LocalInferenceTests.setUp(self)
        raw = b'heretic fixture only'
        mock = patch.dict(local_runtime.HERETIC_SPEC, modelBytes=len(raw),
                          modelSha256=hashlib.sha256(raw).hexdigest())
        mock.start()
        self.addCleanup(mock.stop)
        self.profile = local_variants.select_profile(self.local.settings, True, False)
        Path(self.profile['modelPath']).parent.mkdir(parents=True)
        Path(self.profile['modelPath']).write_bytes(raw)
        Path(self.profile['manifestPath']).write_text(json.dumps(local_runtime.HERETIC_SPEC))
        atomic_json(local_variants.receipt_path(self.profile), dict(schema=local_variants.RECEIPT_SCHEMA,
            passed=True, profile=local_variants.profile_key(self.profile),
            manifestSha256=local_runtime.digest_file(self.profile['manifestPath']),
            modelStamp=local_variants.stamp(self.profile['modelPath']),
            runtimeStamp=local_variants.stamp(self.profile['runtimePath'])))
        self.selected = []
        selected = self.selected
        class Capture(existing.FakeRuntime):
            def __init__(self, settings, *args):
                super().__init__(settings, *args)
                selected.append(dict(settings))
        self.local.runtime_factory = Capture

    def test_choice_survives_acceptance_and_configuration_change(self):
        sent = self.send(qwenUncensored=True, modelPath='untrusted-browser-path')
        self.assertEqual(sent.status_code, 202, sent.json)
        rid = sent.json['runId']
        self.local.settings['contextTokens'] = 8192
        self.s.work_once()
        saved = self.s.snapshot(rid)
        self.assertEqual(saved['status'], 'completed', saved)
        self.assertEqual(saved['localQueue']['modelVariant'], 'heretic')
        self.assertEqual(self.selected[-1]['contextTokens'], 4096)
        self.assertEqual(self.selected[-1]['modelPath'], self.profile['modelPath'])

    def test_default_and_heretic_continue_the_same_history(self):
        first = self.send(message='Remember LABEL-ABC').json['runId']
        self.s.work_once()
        second = self.send(request_id='second-turn', qwenUncensored=True,
                           previousRunId=first, message='What was the label?')
        self.assertEqual(second.status_code, 202, second.json)
        self.s.work_once()
        self.assertEqual([p['modelVariant'] for p in self.selected], ['standard','heretic'])
        self.assertIn('LABEL-ABC', json.dumps(existing.FakeRuntime.requests[-1]))
        self.assertEqual(self.s.snapshot(first)['localQueue']['modelVariant'], 'standard')
        self.assertEqual(self.s.snapshot(second.json['runId'])['localQueue']['modelVariant'], 'heretic')

    def test_missing_variant_rejected_and_does_not_fall_back(self):
        local_variants.receipt_path(self.profile).unlink()
        with patch.object(self.s, 'call', side_effect=AssertionError('cloud')):
            result = self.send(qwenUncensored=True)
        self.assertEqual(result.status_code, 409, result.json)
        self.assertEqual(self.selected, [])

    def test_disable_after_queue_acceptance_fails_honestly(self):
        rid = self.send(qwenUncensored=True).json['runId']
        local_variants.receipt_path(self.profile).unlink()
        self.s.work_once()
        self.assertEqual(self.s.snapshot(rid)['status'], 'error')
        self.assertEqual(self.selected, [])

    def test_invalid_boolean_is_not_coerced(self):
        for bad in ('true', 1, None):
            result = self.send(qwenUncensored=bad)
            self.assertEqual(result.status_code, 400, result.json)

    def test_preference_save_reload_and_stale_tab_rejection(self):
        value = self.client.get('/api/venture/preferences', headers=self.a).json
        settings = existing.local_settings(value['settings'])
        settings['qwenUncensored'] = True
        result = self.client.put('/api/venture/preferences', headers=self.a, json=dict(
            settings=settings, preferenceRevision=value['preferenceRevision'],
            defaultEpoch=value['defaultEpoch'], explicitModelSelection=True))
        self.assertEqual(result.status_code, 200, result.json)
        restored = self.client.get('/api/venture/preferences', headers=self.a).json
        self.assertTrue(restored['settings']['qwenUncensored'])
        stale = self.send(preferenceRevision=value['preferenceRevision'], qwenUncensored=False)
        self.assertEqual(stale.status_code, 409)
        fresh = self.send(preferenceRevision=restored['preferenceRevision'], qwenUncensored=True)
        self.assertEqual(fresh.status_code, 202, fresh.json)
