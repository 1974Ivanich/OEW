import math
import unittest

from tools.bm1109_coenergy import coenergy_torque_nm
from tools.bm1109_inductance import (
    salient_pmsm_labc, salient_pmsm_labc_derivative,
)


class TestCoenergyTorque(unittest.TestCase):
    def test_salient_pmsm_matches_dq_reference_at_37_degrees(self):
        ld, lq, l0 = 4e-3, 7e-3, 5e-3
        psi_f, pole_pairs = 0.05, 4
        id_, iq = 2.0, 3.0
        angle = math.radians(37.0)
        c, s = math.cos(angle), math.sin(angle)
        r = ((c, -s), (s, c))
        i_ab = (c*id_ - s*iq, s*id_ + c*iq)
        b = ((1.0, 0.0), (-0.5, math.sqrt(3)/2),
             (-0.5, -math.sqrt(3)/2))
        i_abc = tuple(
            b[k][0]*i_ab[0] + b[k][1]*i_ab[1] for k in range(3)
        )
        d_l = salient_pmsm_labc_derivative(37.0, ld, lq, l0)

        # d(psi_abc)/d(theta_e) = B * R(theta) * [0, psi_f].
        dpsi_ab = (-s*0.0 - c*psi_f, c*0.0 - s*psi_f)
        # Equivalent direct derivative of R(theta)[psi_f, 0].
        dpsi_ab = (-s*psi_f, c*psi_f)
        dpsi_abc = tuple(
            b[k][0]*dpsi_ab[0] + b[k][1]*dpsi_ab[1] for k in range(3)
        )
        result = coenergy_torque_nm(i_abc, d_l, dpsi_abc, pole_pairs)

        self.assertAlmostEqual(result.pm_nm, 0.900, places=12)
        self.assertAlmostEqual(result.reluctance_nm, -0.108, places=12)
        self.assertAlmostEqual(result.total_nm, 0.792, places=12)
        self.assertAlmostEqual(
            result.total_nm,
            1.5*pole_pairs*(psi_f*iq + (ld-lq)*id_*iq),
            places=12,
        )

    def test_generator_positive_current_changes_pm_sign_only(self):
        ld, lq, l0 = 4e-3, 7e-3, 5e-3
        angle = math.radians(37)
        c, s = math.cos(angle), math.sin(angle)
        i_ab = (c*2-s*3, s*2+c*3)
        b = ((1.0, 0.0), (-0.5, math.sqrt(3)/2),
             (-0.5, -math.sqrt(3)/2))
        i_motor = tuple(b[k][0]*i_ab[0]+b[k][1]*i_ab[1] for k in range(3))
        d_l = salient_pmsm_labc_derivative(37, ld, lq, l0)
        dpsi_ab = (-s*0.05, c*0.05)
        dpsi = tuple(b[k][0]*dpsi_ab[0]+b[k][1]*dpsi_ab[1] for k in range(3))
        motor = coenergy_torque_nm(i_motor, d_l, dpsi, 4)
        generator = coenergy_torque_nm(tuple(-x for x in i_motor), d_l, dpsi, 4)
        self.assertAlmostEqual(generator.pm_nm, -motor.pm_nm, places=12)
        self.assertAlmostEqual(generator.reluctance_nm, motor.reluctance_nm, places=12)

    def test_rejects_bad_inputs(self):
        with self.assertRaises(ValueError):
            coenergy_torque_nm((1, 2, 3), ((1, 0, 0),)*3, (1, 2, 3), True)
        with self.assertRaises(ValueError):
            coenergy_torque_nm((1, 2), ((1, 0, 0),)*3, (1, 2, 3), 2)
        with self.assertRaises(ValueError):
            coenergy_torque_nm((1, 2, 3), ((1, 0, 1), (0, 1, 0), (0, 0, 1)),
                               (1, 2, 3), 2)


if __name__ == "__main__":
    unittest.main()
