import unittest

import sense_test


class SenseTestLogicTests(unittest.TestCase):
    def test_parse_fields_parses_signed_values(self):
        fields = sense_test.parse_fields("I,ia1=-1.2500,ib1=1.2500")
        self.assertEqual(fields, {"ia1": -1.25, "ib1": 1.25})

    def test_robust_noise_ignores_single_outlier(self):
        center, noise = sense_test.robust_noise([100.0] * 19 + [10000.0])
        self.assertEqual(center, 100.0)
        self.assertEqual(noise, 0.0)

    def test_calculate_scale_uses_signed_points_and_reports_linearity(self):
        points = [
            {"duty": -100, "meter_current": -1.0, "raw_median": {"A1": -100.0}, "raw_noise_mad": {"A1": 0.0}},
            {"duty": 100, "meter_current": 1.0, "raw_median": {"A1": 100.0}, "raw_noise_mad": {"A1": 0.0}},
            {"duty": 200, "meter_current": 2.0, "raw_median": {"A1": 200.0}, "raw_noise_mad": {"A1": 0.0}},
        ]
        config = sense_test.DEFAULT_CONFIG.copy()
        scale, quality = sense_test.calculate_scale(points, {"off_a1": 0.0}, {"A1": 0.0}, "A1", config)
        self.assertAlmostEqual(scale, 0.01)
        self.assertEqual(quality["status"], "PASS")
        self.assertAlmostEqual(quality["max_residual_pct"], 0.0)

    def test_calculate_scale_rejects_insufficient_response(self):
        point = {"duty": 100, "meter_current": 1.0, "raw_median": {"A1": 10.0}, "raw_noise_mad": {"A1": 0.0}}
        config = sense_test.DEFAULT_CONFIG.copy()
        with self.assertRaises(ValueError):
            sense_test.calculate_scale([point], {"off_a1": 0.0}, {"A1": 0.0}, "A1", config)

    def test_parse_duties_rejects_zero(self):
        with self.assertRaises(Exception):
            sense_test.parse_duties("-500,0,500")

    def test_applied_line_voltage(self):
        self.assertAlmostEqual(sense_test.applied_line_voltage(100.0, 850, 8500), 20.0)

    def test_phase_resistances_from_line_resistances(self):
        resistances = sense_test.phase_resistances({"AB": 3.0, "BC": 5.0, "CA": 4.0})
        self.assertEqual(resistances, {"A": 1.0, "B": 2.0, "C": 3.0})

    def test_phase_resistances_reject_invalid_combination(self):
        with self.assertRaises(ValueError):
            sense_test.phase_resistances({"AB": 1.0, "BC": 5.0, "CA": 1.0})


if __name__ == "__main__":
    unittest.main()
