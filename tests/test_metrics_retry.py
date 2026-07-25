"""
Tests for core.metrics._with_retry — the Yahoo-throttling backoff wrapper
around get_metrics' load-bearing fetches (.info, market cap, price, daily
history). time.sleep is patched out; no network.
Run with: python -m unittest tests.test_metrics_retry
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stockanalysis.core import metrics


class TestWithRetry(unittest.TestCase):
    def setUp(self):
        self.sleeps = []
        self._orig_sleep = metrics.time.sleep
        metrics.time.sleep = lambda s: self.sleeps.append(s)

    def tearDown(self):
        metrics.time.sleep = self._orig_sleep

    def test_success_first_try_no_sleep(self):
        self.assertEqual(metrics._with_retry(lambda: 42, "NVDA", "price"), 42)
        self.assertEqual(self.sleeps, [])

    def test_throttle_then_success_retries_with_backoff(self):
        calls = {"n": 0}
        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise Exception("Too Many Requests. Rate limited. Try after a while.")
            return "ok"
        self.assertEqual(metrics._with_retry(flaky, "NVDA", ".info"), "ok")
        self.assertEqual(calls["n"], 3)
        self.assertEqual(self.sleeps, [2.0, 4.0])   # exponential backoff

    def test_persistent_throttle_raises_after_max_attempts(self):
        calls = {"n": 0}
        def always_throttled():
            calls["n"] += 1
            raise Exception("HTTP Error 429: Too Many Requests")
        with self.assertRaises(Exception):
            metrics._with_retry(always_throttled, "NVDA", "daily history")
        self.assertEqual(calls["n"], metrics.RETRY_ATTEMPTS)

    def test_non_throttle_error_raises_immediately_no_retry(self):
        calls = {"n": 0}
        def delisted():
            calls["n"] += 1
            raise Exception("Quote not found for symbol: ANSS")
        with self.assertRaises(Exception):
            metrics._with_retry(delisted, "ANSS", ".info")
        self.assertEqual(calls["n"], 1)             # no pointless retries
        self.assertEqual(self.sleeps, [])

    def test_throttle_detection_wording_variants(self):
        for msg in ("Rate limited", "too many requests", "HTTP Error 429"):
            self.assertTrue(metrics._is_throttle(Exception(msg)), msg)
        self.assertFalse(metrics._is_throttle(Exception("possibly delisted")))


if __name__ == "__main__":
    unittest.main()
