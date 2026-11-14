#!/usr/bin/env python3
"""Pure numerical report guards; no GPU/backend."""
import unittest
from hd2d_profile_report import rss_trend, stats


class ProfileStatistics(unittest.TestCase):
    def test_empty_is_not_fake_zero_ms(self):
        self.assertEqual(stats([]), {"count": 0})

    def test_long_frames_are_never_trimmed(self):
        values = [2.0] * 99 + [120.0]
        result = stats(values)
        self.assertEqual(result['count'], 100)
        self.assertAlmostEqual(result['avg'], 3.18)
        self.assertEqual(result['p95'], 2.0)
        self.assertEqual(result['p99'], 120.0)
        self.assertEqual(result['max'], 120.0)
        self.assertEqual(result['over_50'], 1)
        self.assertEqual(len(values), 100)

    def test_rss_compares_warm_windows_not_pre_exec_memory(self):
        samples = [{"seconds": t, "rss_kib": mib*1024}
                   for t, mib in [(0, 1), (30, 100), (40, 104), (50, 102),
                                  (90, 108), (100, 110), (120, 110)]]
        result = rss_trend(samples)
        self.assertTrue(result["comparable"])
        self.assertEqual(result["first_median_mib"], 102)
        self.assertEqual(result["last_median_mib"], 110)
        self.assertEqual(result["delta_mib"], 8)
        self.assertEqual(samples[0]["rss_kib"], 1024)

    def test_short_or_missing_rss_does_not_invent_trend(self):
        self.assertFalse(rss_trend([])["comparable"])
        self.assertFalse(rss_trend([{"seconds": 45, "rss_kib": 1024}])["comparable"])

    def test_single_sample_and_strict_budget(self):
        result = stats([1000 / 60])
        self.assertEqual(result['p99'], result['avg'])
        self.assertEqual(result['over_16_667'], 0)


if __name__ == '__main__':
    unittest.main()
