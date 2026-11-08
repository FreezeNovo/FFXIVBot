"""Regression checks for the deployment test harness on Windows."""
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import test_gateway
import test_p3_na_freshness


class WindowsTestCompatibilityTests(unittest.TestCase):
    def test_gateway_handles_empty_capture_streams_on_success(self):
        result = subprocess.CompletedProcess([sys.executable], 0, stdout='', stderr=None)
        with patch('test_gateway.subprocess.run', return_value=result):
            test_gateway.GatewayTests().test_real_event_permissions_and_send_outcomes()

    def test_gateway_reports_child_failure_instead_of_capture_type_error(self):
        result = subprocess.CompletedProcess([sys.executable], 9, stdout='child failure fixture', stderr=None)
        with patch('test_gateway.subprocess.run', return_value=result):
            with self.assertRaisesRegex(AssertionError, 'child failure fixture'):
                test_gateway.GatewayTests().test_real_event_permissions_and_send_outcomes()

    def test_timezone_check_works_without_posix_tzset(self):
        fixture = test_p3_na_freshness.NAFreshnessTests('test_display_is_beijing_even_with_other_host_timezone')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with patch.dict(time.__dict__):
            time.__dict__.pop('tzset', None)
            fixture.test_display_is_beijing_even_with_other_host_timezone()
