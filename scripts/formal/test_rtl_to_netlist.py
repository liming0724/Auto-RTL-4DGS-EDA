import unittest

from rtl_to_netlist import validate_observations


class ObservationWidthsTest(unittest.TestCase):
    def test_full_width(self):
        validate_observations({"netnames": {"remainder_q": {"bits": list(range(18))}}},
                              {"remainder_q": 18})

    def test_no_observations(self):
        validate_observations({"netnames": {}}, {})

    def test_trimmed_width(self):
        with self.assertRaisesRegex(ValueError, "expected 18 bits, got 12"):
            validate_observations({"netnames": {"remainder_q": {"bits": list(range(12))}}},
                                  {"remainder_q": 18})

    def test_missing_signal(self):
        with self.assertRaisesRegex(ValueError, "expected 18 bits, got 0"):
            validate_observations({"netnames": {}}, {"remainder_q": 18})


if __name__ == "__main__":
    unittest.main()
