"""Synthetic timing contracts; never advertised as real model measurements."""
import json
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from local_calibration import UltraCalibration, identity


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)
        @contextmanager
        def connect():
            db=sqlite3.connect(self.path/'test.db')
            try:
                with db:yield db
            finally:db.close()
        self.db = connect
        local = SimpleNamespace(db=self.db, app=SimpleNamespace(config={'DATA_DIR':self.path,
            'AUDIT_LOGS':SimpleNamespace(directory=self.path/'Desktop'/'Vision Logs')}))
        self.cal = UltraCalibration(local)
        self.cal.initialize()
        self.profile = dict(modelVariant='ultra',ultraReceipt='hardware-v1',threads=8,gpuLayers=4,
            contextTokens=8192,thinking=True,ultraBenchmark=dict(loadSeconds=10,promptTokensPerSecond=100,generationTokensPerSecond=2))

    def row(self, n=0, **changes):
        row = dict(id=str(n), status='completed', inputs_json='[]', max_tokens=1000,
            local_started_at=100, local_finished_at=220, local_profile_json=json.dumps(self.profile),
            local_metrics_json=json.dumps(dict(inputTokens=1000,usage=dict(completion_tokens=200),
                timings=dict(prompt_per_second=100,predicted_per_second=2))))
        return dict(row, **changes)

    def test_ten_samples_freeze_share_and_export_numeric_only(self):
        for n in range(9): self.cal.record(self.row(n, message='PRIVATE QUESTION', owner_uid='PRIVATE ACCOUNT'))
        running=self.row(status='in_progress')
        self.assertEqual(self.cal.predict(running,self.profile,20,0)['estimateBasis'],'benchmark')
        self.cal.record(self.row(9))
        predicted=self.cal.predict(running,self.profile,20,0)
        self.assertEqual((predicted['estimateSamples'],predicted['estimatedTotalSeconds']), (10,120))
        self.assertEqual(predicted['estimatedCompletionAt'],220)
        self.cal.record(self.row(10,local_finished_at=10000))
        self.assertEqual(self.cal.predict(running,self.profile,20,0),predicted)
        reports=list((self.path/'Desktop'/'Vision Logs'/'Qwen Ultra calibration').glob('*.json'))
        self.assertEqual(len(reports),1)
        report=reports[0].read_text()
        self.assertNotIn('PRIVATE',report)
        self.assertEqual(len(json.loads(report)['measurements']),10)
        # Same installation/settings share the profile regardless of user or prompt.
        self.assertEqual(identity(running,self.profile),identity(dict(running,owner_uid='other',message='other'),self.profile))
        self.assertNotEqual(identity(running,self.profile),identity(running,dict(self.profile,thinking=False)))
        self.assertNotEqual(identity(running,self.profile),identity(running,dict(self.profile,ultraReceipt='v2')))

    def test_failed_cancelled_incomplete_missing_and_duplicate_samples_excluded(self):
        for status in ('error','cancelled','incomplete'): self.cal.record(self.row(status=status))
        self.cal.record(self.row(local_metrics_json='{}'))
        self.cal.record(self.row());self.cal.record(self.row())
        with self.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM ultra_timing_samples').fetchone()[0],1)

    def test_waiting_preparation_and_overrun_do_not_claim_completion(self):
        row=self.row(status='in_progress')
        self.assertIsNone(self.cal.predict(row,self.profile,0,2)['estimatedCompletionAt'])
        self.assertIsNone(self.cal.predict(dict(row,local_metrics_json='{}',inputs_json='[{}]'),self.profile,10,0)['estimatedCompletionAt'])
        estimate=self.cal.predict(row,self.profile,10000,0)
        self.assertEqual(estimate['estimateStatus'],'overrun')
        self.assertEqual(estimate['estimatedCompletionAt'],620)
        self.assertEqual(self.cal.predict(self.row(),self.profile,120,0)['estimateStatus'],'complete')

if __name__=='__main__':unittest.main()
