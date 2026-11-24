import math
import unittest
from tools.bm1109_braking import BemfTable, simulate_phase_rl
from tools.bm1109_star_abc import simulate_star_abc

def sine_table(samples=360):
    angles=tuple(360.0*k/samples for k in range(samples))
    rows=tuple((10*math.sin(math.radians(a)),
                10*math.sin(math.radians(a-120)),
                10*math.sin(math.radians(a+120))) for a in angles)
    return BemfTable(angles,rows,1000.0,4)

class TestCoupledStar(unittest.TestCase):
    def test_balanced_star_and_energy_balance(self):
        result=simulate_star_abc(sine_table(),1000,(0.4,)*3,(1.6,)*3,
            ((0.9e-3,0.1e-3,0.1e-3),(0.1e-3,0.9e-3,0.1e-3),(0.1e-3,0.1e-3,0.9e-3)),
            steps_per_period=2048,periodic_tolerance_a=1e-8)
        self.assertTrue(result.converged)
        self.assertLess(abs(result.energy_balance_error_pct),0.1)
        self.assertAlmostEqual(result.phase_rms_current_a[0],result.phase_rms_current_a[1],places=6)
        self.assertAlmostEqual(result.phase_rms_current_a[1],result.phase_rms_current_a[2],places=6)

    def test_matches_independent_baseline_on_balanced_subspace(self):
        table=sine_table()
        mutual=0.1e-3
        phase_l=0.8e-3
        matrix=tuple(tuple(phase_l+mutual if i==j else mutual for j in range(3)) for i in range(3))
        coupled=simulate_star_abc(table,1000,(0.4,)*3,(1.6,)*3,matrix,
                                  steps_per_period=2048,periodic_tolerance_a=1e-8)
        baseline=simulate_phase_rl(table,1000,(0.4,)*3,(1.6,)*3,phase_l,
                                   steps_per_period=2048,periodic_tolerance_a=1e-8)
        self.assertTrue(coupled.converged and baseline.converged)
        self.assertAlmostEqual(coupled.average_braking_torque_nm,baseline.average_braking_torque_nm,delta=0.02)

    def test_rejects_invalid_inductance_matrix(self):
        table=sine_table()
        with self.assertRaises(ValueError):
            simulate_star_abc(table,1000,(1,)*3,(1,)*3,
                ((1e-3,0,0),(1e-4,1e-3,0),(0,0,1e-3)))
        with self.assertRaises(ValueError):
            simulate_star_abc(table,1000,(1,)*3,(1,)*3,
                ((1e-3,2e-3,0),(2e-3,1e-3,0),(0,0,1e-3)))

if __name__=="__main__":
    unittest.main()
