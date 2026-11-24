import math
import unittest

from tools.bm1109_inductance import PeriodicInductanceTable


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


if __name__ == "__main__":
    unittest.main()
