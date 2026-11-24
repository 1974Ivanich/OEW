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

    def test_salient_consistent_bemf_closes_mechanical_and_electrical_power(self):
        # BEMF and L(theta) are derived from the same synthetic rotor reference.
        samples = 96
        rpm, pole_pairs, psi_f = 600.0, 2, 0.05
        omega_e = 2.0*math.pi*rpm/60.0*pole_pairs
        angles = tuple(360.0*k/samples for k in range(samples))
        b = ((1.0, 0.0), (-0.5, math.sqrt(3)/2),
             (-0.5, -math.sqrt(3)/2))
        phase_rows = []
        for angle in angles:
            th = math.radians(angle)
            # d/d(theta_e) [R(theta_e) * (psi_f, 0)] = R * (0, psi_f)
            dpsi_ab = (-math.sin(th)*psi_f, math.cos(th)*psi_f)
            phase_rows.append(tuple(
                omega_e*(b[k][0]*dpsi_ab[0]+b[k][1]*dpsi_ab[1])
                for k in range(3)
            ))
        bemf = BemfTable(angles, tuple(phase_rows), rpm, pole_pairs)
        ld, lq, l0 = 4e-3, 7e-3, 5e-3
        l_angles = tuple(360.0*k/192 for k in range(192))
        l_matrices = tuple(
            salient_pmsm_labc(a, ld, lq, l0) for a in l_angles
        )
        ltable = PeriodicInductanceTable(l_angles, l_matrices)
        result = simulate_variable_l_star(
            bemf, ltable, rpm, (0.5,)*3, (1.0,)*3,
            steps_per_period=1536, max_periods=120,
            periodic_tolerance_a=1e-8
        )
        self.assertTrue(result.converged)
        omega_m = 2.0*math.pi*rpm/60.0
        braking_power = result.average_braking_torque_nm*omega_m
        total_loss = result.copper_loss_w + result.load_loss_w
        self.assertGreater(abs(result.average_reluctance_conversion_w), 1e-3)
        self.assertAlmostEqual(
            braking_power,
            result.average_em_power_w-result.average_reluctance_conversion_w,
            delta=2e-3
        )
        self.assertAlmostEqual(braking_power, total_loss, delta=2e-3)
        self.assertLess(abs(result.energy_balance_error_w), 2e-3)

    def test_zero_speed_returns_zero(self):
        result = simulate_variable_l_star(
            self.make_bemf(), self.make_ltable(), 0.0, (0.5,)*3, (1.0,)*3
        )
        self.assertEqual(result.average_braking_torque_nm, 0.0)
        self.assertEqual(result.average_em_power_w, 0.0)


if __name__ == "__main__":
    unittest.main()
