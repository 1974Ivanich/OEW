# BM1109 — co-energy torque check for a salient PMSM

## Scope

Independent analytical check of the co-energy expression for the synthetic
sinusoidal salient-pole model used by `salient_pmsm_labc()`. The numbers below
are illustrative only and are not BM1109 measurements.

## Coordinate and scaling convention

The Clarke transform is amplitude-invariant:

```
i_ab = C i_abc
C = [[2/3, -1/3, -1/3],
     [0, 1/sqrt(3), -1/sqrt(3)]]
```

The inverse phase reconstruction for a zero-sequence-free vector is
`i_abc = B i_ab`, where
`B = [[1,0],[-1/2,sqrt(3)/2],[-1/2,-sqrt(3)/2]]`.
For this convention, instantaneous power is
`p_abc = (3/2) (v_alpha i_alpha + v_beta i_beta)`.
That 3/2 factor must not be dropped in the torque comparison.

Let electrical rotor angle be theta_e and
`R(theta)=[[cos(theta),-sin(theta)],[sin(theta),cos(theta)]]`.
Then

```
L_ab(theta) = R(theta) diag(Ld,Lq) R(theta)^T
psi_ab(theta) = R(theta) [psi_f, 0]^T
L_abc(theta) = B L_ab(theta) C + (L0/3) 11^T
psi_abc(theta) = B psi_ab(theta)
```

For balanced phase current, `i_abc=B i_ab`, and rotor currents are
`[id, iq]^T = R(theta)^T i_ab`.

## Co-energy derivation

For the linear magnetic model,
`W' = 1/2 i_abc^T L_abc(theta_e) i_abc
       + i_abc^T psi_abc(theta_e)`.
At fixed phase currents, with theta_m=theta_e/p,

```
Te = p [ 1/2 i_abc^T (dL_abc/dtheta_e) i_abc
         + i_abc^T (dpsi_abc/dtheta_e) ]
   = (3/2) p [ (Ld-Lq) id iq + psi_f iq ].
```

The zero-sequence term contributes no torque for a balanced three-wire current.
The reluctance term is essential when Ld != Lq. The 3/2 factor follows from
the amplitude-invariant Clarke convention and the stated phase reconstruction.

## Numerical cross-check (synthetic values)

Use Ld=4 mH, Lq=7 mH, L0=5 mH, psi_f=0.05 Wb, p=4,
id=2 A and iq=3 A, at theta_e=37 degrees.

- PM torque: (3/2)*p*psi_f*iq = +0.900 N m.
- Reluctance torque: (3/2)*p*(Ld-Lq)*id*iq = -0.108 N m.
- Total motor-convention torque: +0.792 N m.

Direct evaluation of the phase-domain co-energy derivative gives the same
components: +0.900 N m and -0.108 N m (finite-difference check of the analytic
matrix derivative). The result is independent of theta_e for fixed rotor-frame
currents id, iq, as expected for the ideal sinusoidal model.

## Generator-current sign

The expression above uses motor-convention current entering the machine and
positive PM flux on the d axis. If the model instead defines generator-positive
current flowing out of the machine, substitute
`i_motor = -i_generator` before evaluating co-energy torque. Consequently,
the PM term changes sign while the quadratic reluctance term does not:
`Te = (3/2)p[-psi_f iq_gen + (Ld-Lq) id_gen iq_gen]`.
Do not change only the total torque sign without checking the declared current
and rotor-angle references.

For positive mechanical rotation, a passive braking torque must oppose that
rotation. This sign check is separate from the positive magnitude of generated
electrical power.

## Validation conclusion and limits

The co-energy formula is consistent with the sinusoidal salient dq reference
provided the 3/2 Clarke scaling is retained. The check validates the analytic
saliency term and PM term, not a BM1109 parameter set or a six-terminal OEW
topology. It assumes a linear, sinusoidal, angle-dependent inductance and
constant PM flux; saturation, harmonics, cross-saturation and topology-specific
zero-sequence paths are excluded.
