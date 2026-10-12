import json
import unittest

from local_timing import estimate


def row(seconds=120,**changes):
    return dict(status='completed',max_tokens=1024,inputs_json='[]',local_profile_json='{}',
                local_started_at=1000,local_finished_at=1000+seconds,**changes)


class LocalTimingTests(unittest.TestCase):
    def test_learns_three_comparable_answers_and_includes_queue(self):
        current=row();history=[row(100),row(120),row(140)]
        self.assertIsNone(estimate(current,history[:2],0,0)['estimatedRemainingSeconds'])
        value=estimate(current,history,20,0)
        self.assertEqual(value['estimateStatus'],'estimated')
        self.assertEqual(value['estimatedRemainingSeconds'],100)
        self.assertEqual(value['estimateSamples'],3)
        self.assertGreater(estimate(current,history,0,2)['estimatedRemainingSeconds'],200)
        self.assertEqual(estimate(current,history,300,0)['estimateStatus'],'overrun')

    def test_failures_other_resource_profiles_and_media_are_not_calibration(self):
        current=row();history=[row() for _ in range(3)]
        for item in history:item['status']='error'
        self.assertEqual(estimate(current,history,0,0)['estimateSamples'],0)
        for item in history:item.update(status='completed',local_profile_json=json.dumps({'threads':16}))
        self.assertEqual(estimate(current,history,0,0)['estimateSamples'],0)
        for item in history:item.update(local_profile_json='{}',inputs_json=json.dumps([{'name':'photo.jpg','mime':'image/jpeg'}]))
        self.assertEqual(estimate(current,history,0,0)['estimateSamples'],0)
        for item in history:item.update(inputs_json='[]',local_finished_at=float('nan'))
        self.assertEqual(estimate(current,history,0,0)['estimateSamples'],0)

    def test_output_and_known_prompt_size_must_be_comparable(self):
        current=row(local_metrics_json=json.dumps({'inputTokens':200}))
        history=[row(local_metrics_json=json.dumps({'inputTokens':3000})) for _ in range(3)]
        self.assertEqual(estimate(current,history,0,0)['estimateSamples'],0)
        for item in history:item.update(local_metrics_json='{}',max_tokens=2048)
        self.assertEqual(estimate(current,history,0,0)['estimateSamples'],0)
