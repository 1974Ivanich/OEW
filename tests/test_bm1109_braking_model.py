import math
import unittest

from tools.bm1109_braking import (
    BemfTable,
    simulate_phase_rl,
    sinusoidal_analytical_torque,
)


def sine_table(samples=720, rpm=1000.0, pole_pairs=4, phase_peak=10.0, phase_offset_deg=0.0):
    angles = tuple(360.0 * k / samples for k in range(samples))
    rows = tuple((
        phase_peak * math.sin(math.radians(a + phase_offset_deg)),
        phase_peak * math.sin(math.radians(a - 120.0 + phase_offset_deg)),
        phase_peak * math.sin(math.radians(a + 120.0 + phase_offset_deg)),
    ) for a in angles)
    return BemfTable(angles, rows, rpm, pole_pairs)


class TestBM1109BrakingModel(unittest.TestCase):
    def test_zero_speed_has_zero_electrical_braking(self):
        result = simulate_phase_rl(sine_table(), 0.0, (1, 1, 1), (2, 2, 2), 1e-3)
        self.assertEqual(result.average_braking_torque_nm, 0.0)
        self.assertEqual(result.phase_peak_current_a, (0.0, 0.0, 0.0))

    def test_numerical_matches_sinusoidal_analytical_case(self):
        table = sine_table()
        numerical = simulate_phase_rl(
            table, 1000.0, (0.4, 0.4, 0.4), (1.6, 1.6, 1.6), 0.8e-3,
            steps_per_period=4096, max_periods=80, periodic_tolerance_a=1e-8,
        )
        analytical = sinusoidal_analytical_torque(table, 1000.0, 0.4, 1.6, 0.8e-3)
        self.assertTrue(numerical.converged)
        self.assertAlmostEqual(numerical.average_braking_torque_nm, analytical, delta=0.01)
        self.assertLess(abs(numerical.energy_balance_error_pct), 0.1)

    def test_analytical_reference_is_invariant_to_angle_origin(self):
        table = sine_table(phase_offset_deg=37.0)
        numerical = simulate_phase_rl(
            table, 1000.0, (0.4, 0.4, 0.4), (1.6, 1.6, 1.6), 0.8e-3,
            steps_per_period=4096, max_periods=80, periodic_tolerance_a=1e-8,
        )
        analytical = sinusoidal_analytical_torque(table, 1000.0, 0.4, 1.6, 0.8e-3)
        self.assertTrue(numerical.converged)
        self.assertAlmostEqual(numerical.average_braking_torque_nm, analytical, delta=0.01)

    def test_balanced_fifth_harmonic_bemf_reaches_periodic_solution(self):
        samples = 720
        angles = tuple(360.0 * k / samples for k in range(samples))
        rows = tuple((
            10.0 * math.sin(math.radians(a)) + 1.0 * math.sin(math.radians(5.0 * a)),
            10.0 * math.sin(math.radians(a - 120.0)) + 1.0 * math.sin(math.radians(5.0 * a + 120.0)),
            10.0 * math.sin(math.radians(a + 120.0)) + 1.0 * math.sin(math.radians(5.0 * a - 120.0)),
        ) for a in angles)
        table = BemfTable(angles, rows, 1000.0, 4)
        result = simulate_phase_rl(
            table, 1000.0, (0.4, 0.4, 0.4), (1.6, 1.6, 1.6), 0.8e-3,
            steps_per_period=4096, max_periods=80, periodic_tolerance_a=1e-8,
        )
        self.assertTrue(result.converged)
        self.assertGreater(result.average_braking_torque_nm, 0.0)
        self.assertLess(abs(result.energy_balance_error_pct), 0.1)

    def test_speed_scaling_increases_bemf(self):
        table = sine_table()
        e1 = table.at_angle(90.0, 500.0)[0]
        e2 = table.at_angle(90.0, 1000.0)[0]
        self.assertAlmostEqual(e2 / e1, 2.0)

    def test_rejects_nonuniform_angle_table(self):
        with self.assertRaises(ValueError):
            BemfTable((0.0, 100.0, 240.0), ((0, 1, -1),)*3, 1000.0, 4)


if __name__ == "__main__":
    unittest.main()
