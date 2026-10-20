"""OBS presence and church-clock behavior, without OBS/network/model calls."""
from datetime import timedelta
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import Mock, patch

import psutil
from title_eyes_standby import ObsActivity, church_now, observe_obs


class ObsActivityTests(TestCase):
    def test_process_names_case_and_unknown_scan(self):
        for name in ('OBS', 'obs64.exe', 'obs32.exe', 'OBS.EXE'):
            with patch('psutil.process_iter',return_value=[SimpleNamespace(info={'name':name})]):
                self.assertTrue(observe_obs())
        with patch('psutil.process_iter',return_value=[SimpleNamespace(info={'name':'other.exe'})]):
            self.assertFalse(observe_obs())
        with patch('psutil.process_iter',return_value=[SimpleNamespace(info={'name':None})]):
            self.assertIsNone(observe_obs())
        with patch('psutil.process_iter',side_effect=psutil.AccessDenied()):
            self.assertIsNone(observe_obs())

    def test_cached_presence_and_bounded_recovery_grace(self):
        now=[0.0]
        probe=Mock(return_value=True)
        activity=ObsActivity(probe,lambda:now[0])
        self.assertTrue(activity.active())
        now[0]=4;self.assertTrue(activity.active());self.assertEqual(probe.call_count,1)
        probe.return_value=None
        now[0]=5;self.assertTrue(activity.active())
        now[0]=120;self.assertTrue(activity.active())
        now[0]=125;self.assertFalse(activity.active())
        probe.return_value=True
        now[0]=130;self.assertTrue(activity.active())
        probe.return_value=False
        now[0]=135;self.assertFalse(activity.active())
        # A fresh server instance redetects OBS rather than replaying old state.
        probe.return_value=True
        self.assertTrue(ObsActivity(probe,lambda:now[0]).active())

    def test_unknown_initial_scan_does_not_claim_obs_is_open(self):
        activity=ObsActivity(lambda:None,lambda:0)
        self.assertFalse(activity.active())

    def test_church_clock_is_explicit_and_dst_aware(self):
        now=church_now()
        self.assertEqual(now.tzinfo.key,'America/New_York')
        self.assertEqual(now.replace(month=1,day=15).utcoffset(),timedelta(hours=-5))
        self.assertEqual(now.replace(month=7,day=15).utcoffset(),timedelta(hours=-4))


if __name__=='__main__':main()
