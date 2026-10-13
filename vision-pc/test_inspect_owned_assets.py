"""Read-only inventories use real files/SQLite and never fabricate missing media."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from inspect_owned_assets import inspect, regular_under
import test_owned_assets


class RetentionInventoryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_owned_assets.OwnedAssetTests()
        self.fixture.setUp()
        self.root = self.fixture.root
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({'dataDir': str(self.root), 'durableAssets': {'enabled': True},
                                          'token': 'private-do-not-print-this'}))

    def tearDown(self):
        self.fixture.tearDown()

    def test_read_only_hash_inventory_contains_no_secrets_or_owner_identifiers(self):
        self.fixture.adopt()
        before = self.fixture.database.read_bytes()
        result = inspect(self.config, verify=True)
        self.assertEqual(result['counts']['ownedAssets'], 1)
        self.assertEqual(result['issues'], [])
        self.assertTrue(result['ownedAssets'][0]['sha256Verified'])
        self.assertEqual(self.fixture.database.read_bytes(), before)
        encoded = json.dumps(result)
        for secret in ('private-do-not-print-this', 'firebase:alice', 'https://example.com/public.mp4'):
            self.assertNotIn(secret, encoded)
        self.assertFalse(result['databaseModified'])
        self.assertFalse(result['boardAttachmentImplemented'])

    def test_missing_and_same_length_corrupted_assets_are_not_declared_verified(self):
        row = self.fixture.adopt()
        path = self.fixture.assets.ready_path(row)
        path.write_bytes(b'x' * row['byte_count'])
        result = inspect(self.config, verify=True)
        self.assertFalse(result['ownedAssets'][0]['sha256Verified'])
        self.assertTrue(result['issues'])
        path.unlink()
        result = inspect(self.config)
        self.assertFalse(result['ownedAssets'][0]['exists'])
        self.assertTrue(result['issues'])

    def test_incomplete_adoptions_and_leases_are_reported_without_modification(self):
        a = self.fixture.assets
        a._reserve_asset(self.fixture.uid, self.fixture.job, self.fixture.project, 'source',
                         len(self.fixture.content), 'media.mp4', 'video/mp4', {})
        result = inspect(self.config)
        self.assertEqual(result['ownedAssets'][0]['state'], 'copying')
        self.assertEqual(result['sourceLeases'][0]['vortexId'], self.fixture.low)
        self.assertTrue(self.fixture.leased())
        self.assertTrue(result['issues'])

    def test_no_database_is_created_when_it_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'config.json'
            config.write_text('{}')
            result = inspect(config)
            self.assertEqual(result['issues'], ['database-unavailable'])
            self.assertFalse((root / 'data').exists())

    def test_invalid_configuration_and_linked_results_root_are_rejected(self):
        for options in ([], False, 'yes', {'enabled': 'yes'}):
            self.config.write_text(json.dumps({'durableAssets': options}))
            with self.assertRaises(ValueError):
                inspect(self.config)
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'private.json').write_text('private')
        try:
            (self.root / 'results').symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Creating symlinks requires platform privileges')
        with self.assertRaises(ValueError):
            regular_under(self.root / 'results', outside / 'private.json')


if __name__ == '__main__':
    unittest.main()
