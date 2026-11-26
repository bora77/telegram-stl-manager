import os
import time
import unittest
from datetime import datetime
from unittest.mock import patch
from app.check_schedule import next_check_at


class ClockScheduleTests(unittest.TestCase):
    def setUp(self):
        self.environment=patch.dict(os.environ,{'TZ':'Europe/Berlin'});self.environment.start();time.tzset()

    def tearDown(self):
        self.environment.stop();time.tzset()

    def test_manual_checks_do_not_move_four_hour_slots(self):
        for minute in (1,9,31,59):
            checked=datetime(2026,11,26,12,minute).timestamp()
            self.assertEqual(datetime.fromtimestamp(next_check_at(checked,4*3600)),datetime(2026,11,26,16))
        self.assertEqual(datetime.fromtimestamp(next_check_at(datetime(2026,11,26,15,59).timestamp(),4*3600)),datetime(2026,11,26,16))

    def test_interval_changes_and_exact_boundary(self):
        checked=datetime(2026,11,26,12,9).timestamp()
        self.assertEqual(datetime.fromtimestamp(next_check_at(checked,3600)),datetime(2026,11,26,13))
        self.assertEqual(datetime.fromtimestamp(next_check_at(checked,8*3600)),datetime(2026,11,26,16))
        self.assertEqual(datetime.fromtimestamp(next_check_at(datetime(2026,11,26,16).timestamp(),4*3600)),datetime(2026,11,26,20))

    def test_day_rollover_and_fractional_slots(self):
        self.assertEqual(datetime.fromtimestamp(next_check_at(datetime(2026,11,26,23,30).timestamp(),4*3600)),datetime(2026,11,27))
        self.assertEqual(datetime.fromtimestamp(next_check_at(datetime(2026,11,26,12,9).timestamp(),900)),datetime(2026,11,26,12,15))

    def test_daylight_saving_keeps_local_clock_boundaries(self):
        for date in ((2026,3,29),(2026,10,25)):
            checked=datetime(*date,1,45).timestamp()
            result=next_check_at(checked,4*3600)
            self.assertGreater(result,checked)
            self.assertEqual(datetime.fromtimestamp(result),datetime(*date,4))

    def test_initial_check_is_due_and_invalid_interval_rejected(self):
        self.assertEqual(next_check_at(None,14400),0)
        with self.assertRaises(ValueError):next_check_at(1000,0)
