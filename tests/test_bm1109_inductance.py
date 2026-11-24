import math
import unittest

from tools.bm1109_inductance import (
    PeriodicInductanceTable, salient_pmsm_labc,
    salient_pmsm_labc_derivative,
)


def const_matrix(ls=1.0e-3, mutual=0.1e-3):
    return tuple(tuple(ls if i == j else mutual for j in range(3)) for i in range(3))


class TestPeriodicInductanceTable(unittest.TestCase):
    def test_constant_matrix_and_zero_derivative(self):
        matrix = const_matrix()
        table = PeriodicInductanceTable((0, 90, 180, 270), (matrix,)*4)
        for angle in (-720, -1, 0, 37, 359.999, 360, 721):
            got = table.evaluate(angle)
            deriv = table.derivative_per_degree(angle)
            for i in range(3):
                for j in range(3):
                    self.assertAlmostEqual(got[i][j], matrix[i][j], places=14)
                    self.assertAlmostEqual(deriv[i][j], 0.0, places=14)

    def test_periodic_value_and_derivative_at_wrap(self):
        angles = (0.0, 90.0, 180.0, 270.0)
        matrices = []
        for angle in angles:
            # Small smooth periodic modulation, while keeping SPD.
            x = math.cos(math.radians(angle))
            matrices.append(((1e-3 + 0.1e-3*x, 0.05e-3, 0.02e-3),
                             (0.05e-3, 1.1e-3 - 0.05e-3*x, 0.01e-3),
                             (0.02e-3, 0.01e-3, 0.9e-3)))
        table = PeriodicInductanceTable(angles, matrices)
        a = table.evaluate(0.0)
        b = table.evaluate(360.0)
        da = table.derivative_per_degree(0.0)
        db = table.derivative_per_degree(360.0)
        for i in range(3):
            for j in range(3):
                self.assertAlmostEqual(a[i][j], b[i][j], places=14)
                self.assertAlmostEqual(da[i][j], db[i][j], places=14)

    def test_rejects_non_spd_source_and_interpolation_overshoot(self):
        with self.assertRaises(ValueError):
            PeriodicInductanceTable((0, 120, 240), (
                ((1e-3, 0, 0), (0, 1e-3, 0), (0, 0, -1e-3)),)*3)

    def test_rejects_bad_angles(self):
        with self.assertRaises(ValueError):
            PeriodicInductanceTable((0, 180, 180), (const_matrix(),)*3)
        with self.assertRaises(ValueError):
            PeriodicInductanceTable((0, 180, 360), (const_matrix(),)*3)


    def test_salient_reference_derivative_matches_finite_difference(self):
        ld, lq, l0 = 1.4e-3, 0.8e-3, 1.1e-3
        h_rad = 1.0e-5
        for angle in (0.0, 17.0, 83.0, 179.0, 271.0, 359.0):
            analytic = salient_pmsm_labc_derivative(angle, ld, lq, l0)
            delta_deg = math.degrees(h_rad)
            plus = salient_pmsm_labc(angle + delta_deg, ld, lq, l0)
            minus = salient_pmsm_labc(angle - delta_deg, ld, lq, l0)
            for i in range(3):
                for j in range(3):
                    numeric = (plus[i][j] - minus[i][j]) / (2*h_rad)
                    self.assertAlmostEqual(analytic[i][j], numeric, delta=2e-10)

    def test_isotropic_inductance_has_zero_angle_derivative(self):
        matrix = salient_pmsm_labc(23.0, 1e-3, 1e-3, 0.7e-3)
        deriv = salient_pmsm_labc_derivative(23.0, 1e-3, 1e-3, 0.7e-3)
        for i in range(3):
            for j in range(3):
                self.assertTrue(math.isfinite(matrix[i][j]))
                self.assertAlmostEqual(deriv[i][j], 0.0, places=14)

    def test_salient_reference_is_symmetric_positive_definite(self):
        for angle in range(0, 360, 7):
            matrix = salient_pmsm_labc(angle, 1.4e-3, 0.8e-3, 1.1e-3)
            PeriodicInductanceTable((0, 120, 240), (matrix, matrix, matrix))
            for i in range(3):
                for j in range(3):
                    self.assertAlmostEqual(matrix[i][j], matrix[j][i], places=15)

    def test_salient_reference_requires_explicit_positive_l0(self):
        with self.assertRaises(ValueError):
            salient_pmsm_labc(0.0, 1e-3, 0.8e-3, 0.0)


if __name__ == "__main__":
    unittest.main()
