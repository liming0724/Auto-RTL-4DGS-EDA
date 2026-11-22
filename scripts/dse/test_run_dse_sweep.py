import unittest

from gs4d_flow import tag
from run_dse_sweep import select_configs


class ConfigSelectionTest(unittest.TestCase):
    def test_default_full_grid(self):
        self.assertEqual(len(select_configs(None)), 18)

    def test_subset_deduplicated_in_grid_order(self):
        names = ["pe4_q12_w4_s512", "pe1_q8_w1_s512", "pe1_q8_w1_s512"]
        self.assertEqual([tag(c) for c in select_configs(names)],
                         ["pe1_q8_w1_s512", "pe4_q12_w4_s512"])

    def test_empty(self):
        with self.assertRaises(ValueError):
            select_configs([])

    def test_unknown(self):
        with self.assertRaises(ValueError):
            select_configs(["pe1_q8_w1_s512", "pe3_q8_w1_s512"])


if __name__ == "__main__":
    unittest.main()
