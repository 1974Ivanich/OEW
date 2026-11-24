# Analytical reference: salient PMSM inductance in abc coordinates

## Purpose and status

This note derives a synthetic, analytically checkable reference for a symmetric
salient-pole PMSM. It is intended to validate coordinate transforms,
angle conventions, variable-inductance derivatives and co-energy torque.

It is not a parameter identification of BM1109. No numerical motor parameters
are asserted. It is not a hardware test procedure.

## Conventions

- Electrical rotor angle: `theta_e`, radians, positive in the direction used
  by the Park transform below.
- Pole-pair count: `p`; mechanical angle is `theta_m = theta_e / p`.
- Amplitude-invariant Clarke transform:
  `i_alpha = (2 ia - ib - ic)/3`,
  `i_beta = (ib - ic)/sqrt(3)`.
- Inverse Clarke on the zero-sequence-free subspace:
  `ia = i_alpha`,
  `ib = -i_alpha/2 + sqrt(3)*i_beta/2`,
  `ic = -i_alpha/2 - sqrt(3)*i_beta/2`.
- Park transform:
  `[id, iq]^T = [[cos(theta), sin(theta)],[-sin(theta), cos(theta)]] [i_alpha, i_beta]^T`.
- Positive motor torque follows the standard convention
  `Te = (3/2) p [psi_f iq + (Ld-Lq) id iq]`.

The power identity for this amplitude-invariant transform is
`ia*va + ib*vb + ic*vc = (3/2)(i_alpha*v_alpha + i_beta*v_beta)`
when zero sequence is absent.

## Deriving the stationary alpha-beta inductance

Let `c=cos(theta_e)`, `s=sin(theta_e)`, and assume constant positive
`Ld` and `Lq` in the rotor frame. Then

```
L_ab(theta) = A(theta)^T diag(Ld,Lq) A(theta)
A = [[c, s], [-s, c]]
```

Therefore

```
L_alpha_alpha = Ld*c*c + Lq*s*s
L_alpha_beta  = (Ld-Lq)*c*s
L_beta_beta   = Ld*s*s + Lq*c*c
```

and

```
dL_alpha_alpha/dtheta = 2*(Lq-Ld)*s*c
dL_alpha_beta/dtheta  = (Ld-Lq)*(c*c-s*s)
dL_beta_beta/dtheta   = 2*(Ld-Lq)*s*c
```

These derivatives are per electrical radian. A derivative per mechanical
radian is multiplied by `p`.

## Mapping to abc and the zero-sequence issue

Define

```
C = [[ 2/3, -1/3, -1/3],
     [ 0,    1/sqrt(3), -1/sqrt(3)]]

B = [[1, -1/2, -1/2],
     [0, sqrt(3)/2, -sqrt(3)/2]]^T
```

For the zero-sequence-free subspace, `Labc = B * Lab * C`. This matrix is
symmetric positive semidefinite, not positive definite: the common-mode
vector `[1,1,1]` is in its nullspace. This is expected because the dq model
does not specify zero-sequence inductance.

A full 3x3 positive-definite matrix can be written as

```
Labc_full(theta) = B * Lab(theta) * C + (L0/3) * ones(3,3)
```

where `L0 > 0` is the zero-sequence eigenvalue under this normalization.
This produces eigenvalues `Ld`, `Lq`, and `L0` in the corresponding
orthonormal subspaces. However, `L0` cannot be inferred from `Ld,Lq`.
It must be measured or explicitly marked as an assumed nuisance parameter.

For an ideal three-wire floating-neutral model with
`ia+ib+ic=0`, the added common-mode term does not affect the physical
differential current dynamics or co-energy derivative. It is still important
not to confuse this modelling completion with measured zero-sequence physics.

**Implementation implication:** the existing table utility requires a
positive-definite 3x3 matrix. Therefore a dq-derived test table must provide
an explicit positive `L0`. Do not silently add a numerical epsilon and call
it a measured motor parameter. Alternatively, a future API may support
positive-semidefinite matrices with an explicitly constrained three-wire
subspace.

## PM flux linkage and BEMF

For constant PM flux `psi_f` on the d axis:

```
psi_alpha = psi_f*cos(theta_e)
psi_beta  = psi_f*sin(theta_e)
psi_pm_abc = B * [psi_alpha, psi_beta]^T
dpsi_pm_abc/dtheta_e = B * [-psi_f*sin(theta_e), psi_f*cos(theta_e)]^T
```

The time derivative is multiplied by electrical angular speed
`omega_e = p*omega_m`. The sign of measured open-circuit BEMF depends on
the voltage reference and generator-current convention; it must be aligned
explicitly before inserting measured waveforms into the solver.

Open-circuit BEMF identifies the PM flux derivative (subject to the voltage
and angle conventions), but does not identify `Ld`, `Lq`, or `L0`.

## Co-energy torque derivation

For a linear magnetic model,

```
W' = 1/2 * i_abc^T * Labc(theta_e) * i_abc
     + i_abc^T * psi_pm_abc(theta_e)
Te = p * partial(W')/partial(theta_e) at fixed phase currents
```

With the transforms above, this reduces to

```
Te = (3/2)*p * [psi_f*iq + (Ld-Lq)*id*iq]
```

The two terms are distinct:
- PM torque: `(3/2)*p*psi_f*iq`;
- reluctance torque: `(3/2)*p*(Ld-Lq)*id*iq`.

This is the motor-positive convention. If the simulation defines generator
currents as positive into the machine, the sign mapping must be stated and
tested rather than inferred from variable names.

## Validation gates before solver coupling

1. For `Ld=Lq=L`, the alpha-beta inductance is angle-independent.
2. For `Ld != Lq`, the analytic derivative matches centered finite
   differences over a full electrical revolution.
3. `Labc(theta)` is periodic and symmetric.
4. The three-wire current subspace has `sum(i)=0`; its restricted inductance
   has positive eigenvalues `Ld,Lq`.
5. A chosen positive `L0` makes the full matrix positive definite, but is
   tagged assumed unless independently measured.
6. Co-energy differentiation matches the standard dq torque expression.
7. PM-only torque and reluctance-only torque are tested separately.
8. Electrical power, copper loss, magnetic-energy change and mechanical
   conversion use one explicit sign convention.

## Scope boundary

This reference is for a symmetric three-phase PMSM mathematical test case.
It does not establish the BM1109 winding connection, its zero-sequence path,
its OEW six-terminal topology, mutual coupling between bridges, rectifier
behavior, DC-link behavior, saturation, slot harmonics or actual motor
parameters. Those require separate measured data and topology-specific models.
