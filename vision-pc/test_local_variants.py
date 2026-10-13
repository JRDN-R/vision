"""Offline registry/receipt/installer tests with tiny, explicitly simulated weights."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import local_runtime as runtime
import local_variants as variants
import setup_local_variant as setup


class VariantTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = b'heretic protocol fixture, not real weights'
        self.spec_patch = patch.dict(runtime.HERETIC_SPEC, modelBytes=len(self.raw),
                                    modelSha256=hashlib.sha256(self.raw).hexdigest())
        self.spec_patch.start()
        self.addCleanup(self.spec_patch.stop)
        self.base = self.root / 'standard.gguf'
        self.base.write_bytes(b'original fixture')
        self.exe = self.root / 'runtime' / 'llama-server.exe'
        self.exe.parent.mkdir()
        self.exe.write_bytes(b'runtime fixture')
        self.settings = dict(enabled=True, modelPath=str(self.base), runtimePath=str(self.exe),
                             manifestPath=str(self.root/'standard.json'), contextTokens=4096,
                             threads=8, maxOutputTokens=2048, gpuLayers=16, visualEnabled=True)
        self.profile = variants.select_profile(self.settings, True, require_ready=False)
        Path(self.profile['modelPath']).parent.mkdir(parents=True)
        Path(self.profile['modelPath']).write_bytes(self.raw)
        self.manifest = Path(self.profile['manifestPath'])
        self.manifest.write_text(json.dumps({**runtime.HERETIC_SPEC,
            'runtimeRoot':str(self.exe.parent), 'runtimeFiles':{self.exe.name:runtime.digest_file(self.exe)},
            'runtimeSha256':runtime.digest_file(self.exe)}))

    def ready(self):
        setup.atomic_json(variants.receipt_path(self.profile), dict(
            schema=variants.RECEIPT_SCHEMA, passed=True, profile=variants.profile_key(self.profile),
            manifestSha256=runtime.digest_file(self.manifest),
            modelStamp=variants.stamp(self.profile['modelPath']), runtimeStamp=variants.stamp(self.exe)))

    def test_default_does_not_change_existing_model_or_config(self):
        before = dict(self.settings)
        selected = variants.select_profile(self.settings)
        self.assertEqual(selected['modelPath'], str(self.base))
        self.assertEqual(selected['gpuLayers'], 16)
        self.assertEqual(selected['modelVariant'], 'standard')
        self.assertEqual(self.settings, before)

    def test_boolean_and_variant_are_strict(self):
        for value in ('true', 1, {}, None):
            with self.assertRaises(runtime.LocalFailure):
                variants.select_profile(self.settings, value)
        with self.assertRaises(runtime.LocalFailure):
            runtime.model_spec({'modelVariant':'arbitrary.gguf'})

    def test_download_is_not_readiness_and_no_fallback(self):
        self.assertFalse(variants.variant_status(self.settings)['available'])
        with self.assertRaises(OSError):
            variants.select_profile(self.settings, True)
        self.assertEqual(self.base.read_bytes(), b'original fixture')

    def test_ready_variant_uses_distinct_files_and_cpu_only(self):
        self.ready()
        result = variants.select_profile(self.settings, True)
        self.assertNotEqual(result['modelPath'], self.settings['modelPath'])
        self.assertEqual(result['gpuLayers'], 0)
        self.assertEqual(result['modelVariant'], 'heretic')
        self.assertTrue(variants.variant_status(self.settings)['available'])

    def test_visual_worker_stays_at_original_location(self):
        self.assertEqual(self.profile['visualManifestPath'],
                         str(self.root/'visual'/'visual-worker-manifest.json'))
        explicit = {**self.settings, 'visualManifestPath':str(self.root/'custom-visual.json')}
        self.assertEqual(variants.select_profile(explicit, True, False)['visualManifestPath'], explicit['visualManifestPath'])

    def test_receipt_invalidated_by_model_runtime_manifest_or_profile_changes(self):
        for target in ('model', 'runtime', 'manifest', 'profile'):
            with self.subTest(target=target):
                self.ready()
                if target == 'profile':
                    changed = {**self.profile, 'contextTokens':8192}
                    with self.assertRaises(runtime.LocalFailure):
                        variants.check_ready(changed)
                    continue
                path = {'model':Path(self.profile['modelPath']), 'runtime':self.exe, 'manifest':self.manifest}[target]
                original = path.read_bytes()
                path.write_bytes(original + b' ')
                with self.assertRaises(runtime.LocalFailure):
                    variants.check_ready(self.profile)
                path.write_bytes(original)

    def test_disabled_and_removed_receipts_cannot_advertise_available(self):
        self.ready()
        self.assertFalse(variants.variant_status({**self.settings,'enabled':False})['available'])
        variants.receipt_path(self.profile).unlink()
        self.assertFalse(variants.variant_status(self.settings)['available'])

    def test_provenance_mismatch_is_rejected(self):
        self.ready()
        self.manifest.write_text(json.dumps({**runtime.HERETIC_SPEC,'modelRevision':'wrong'}))
        with self.assertRaisesRegex(runtime.LocalFailure, 'provenance'):
            variants.check_ready(self.profile)

    def test_symlink_receipt_rejected(self):
        self.ready()
        receipt = variants.receipt_path(self.profile)
        copy = receipt.with_name('other.json')
        receipt.rename(copy)
        try:
            receipt.symlink_to(copy)
        except OSError:
            self.skipTest('Symlinks not available for this account')
        with self.assertRaises(ValueError):
            variants.check_ready(self.profile)

    def test_download_never_overwrites_an_existing_model(self):
        destination = Path(self.profile['modelPath'])
        destination.write_bytes(b'wrong')
        with patch.object(setup.requests, 'Session', side_effect=AssertionError('network')):
            with self.assertRaises(runtime.LocalFailure):
                setup.download_model(destination)
        self.assertEqual(destination.read_bytes(), b'wrong')

    def test_full_download_size_and_checksum_verified(self):
        destination = self.root/'download.gguf'
        response = MagicMock(status_code=200, is_redirect=False)
        response.__enter__.return_value = response
        response.iter_content.return_value = [self.raw]
        with patch.object(setup.requests, 'Session') as session:
            session.return_value.__enter__.return_value.get.return_value = response
            setup.download_model(destination)
        self.assertEqual(destination.read_bytes(), self.raw)
        self.assertFalse(Path(str(destination)+'.part').exists())

    def test_corrupt_download_is_not_published(self):
        destination = self.root/'bad-download.gguf'
        response = MagicMock(status_code=200, is_redirect=False)
        response.__enter__.return_value = response
        response.iter_content.return_value = [b'bad']
        with patch.object(setup.requests, 'Session') as session:
            session.return_value.__enter__.return_value.get.return_value = response
            with self.assertRaises(runtime.LocalFailure):
                setup.download_model(destination)
        self.assertFalse(destination.exists())

    def test_smoke_uses_two_turns_and_ignores_private_reasoning(self):
        requests = []
        class Fixture:
            def __init__(self, *args): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def count(self, messages): return 100
            def generate(self, messages, limit):
                requests.append(list(messages))
                yield {'choices':[{'delta':{'reasoning_content':'private fixture only'}}]}
                yield {'choices':[{'delta':{'content':'COBALT-731'},'finish_reason':'stop'}]}
        setup.smoke_test(self.profile, Fixture)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[1][-2]['role'], 'assistant')
        self.assertNotIn('private fixture', json.dumps(requests))

    def test_runtime_checks_heretic_bytes_without_weakening_standard_pins(self):
        instance = runtime.LlamaRuntime(self.profile, lambda:False, lambda text:None)
        try:
            self.assertEqual(instance.verify(), (self.exe, Path(self.profile['modelPath'])))
            instance.settings['modelVariant'] = 'standard'
            with self.assertRaisesRegex(runtime.LocalFailure, 'provenance'):
                instance.verify()
        finally:
            instance.http.close()

    def test_installer_is_shipped_without_altering_original_installer(self):
        source = Path(__file__).with_name('Setup-Vision-PC.ps1').read_text()
        self.assertIn("'setup_local_variant.py'", source)
        self.assertIn("'local_variants.py'", source)
        self.assertNotIn('--action enable', source)


if __name__ == '__main__': unittest.main()
