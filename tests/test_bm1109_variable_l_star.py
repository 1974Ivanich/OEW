import math
import unittest

from tools.bm1109_braking import BemfTable
from tools.bm1109_inductance import PeriodicInductanceTable, salient_pmsm_labc
from tools.bm1109_variable_l_star import simulate_variable_l_star


class TestVariableLStar(unittest.TestCase):
    def make_bemf(self, samples=96, rpm=600.0, pole_pairs=2):
        rows = []
        angles = tuple(360.0*k/samples for k in range(samples))
        for angle in angles:
            th = math.radians(angle)
            rows.append((10*math.sin(th), 10*math.sin(th-2*math.pi/3),
                         10*math.sin(th+2*math.pi/3)))
        return BemfTable(angles, tuple(rows), rpm, pole_pairs)

    def make_ltable(self, ld=4e-3, lq=4e-3, l0=5e-3, samples=48):
        angles = tuple(360.0*k/samples for k in range(samples))
        matrices = tuple(salient_pmsm_labc(a, ld, lq, l0) for a in angles)
        return PeriodicInductanceTable(angles, matrices)

    def test_constant_isotropic_l_matches_star_solver_energy(self):
        bemf = self.make_bemf()
        ltable = self.make_ltable()
        result = simulate_variable_l_star(
            bemf, ltable, 600.0, (0.5,)*3, (1.0,)*3,
            steps_per_period=768, max_periods=120, periodic_tolerance_a=1e-8
        )
        self.assertTrue(result.converged)
        self.assertLess(result.current_sum_peak_a, 1e-10)
        self.assertLess(abs(result.average_reluctance_conversion_w), 1e-10)
        self.assertLess(abs(result.energy_balance_error_pct), 0.1)
        self.assertGreater(result.average_braking_torque_nm, 0.0)

    def test_salient_model_has_nonzero_reluctance_conversion(self):
        bemf = self.make_bemf()
        ltable = self.make_ltable(ld=4e-3, lq=7e-3)
        result = simulate_variable_l_star(
            bemf, ltable, 600.0, (0.5,)*3, (1.0,)*3,
            steps_per_period=1024, max_periods=120
        )
        self.assertTrue(result.converged)
        self.assertGreater(abs(result.average_reluctance_conversion_w), 1e-6)
        self.assertLess(abs(result.energy_balance_error_pct), 0.5)

    def test_zero_speed_returns_zero(self):
        result = simulate_variable_l_star(
            self.make_bemf(), self.make_ltable(), 0.0, (0.5,)*3, (1.0,)*3
        )
        self.assertEqual(result.average_braking_torque_nm, 0.0)
        self.assertEqual(result.average_em_power_w, 0.0)


if __name__ == "__main__":
    unittest.main()
