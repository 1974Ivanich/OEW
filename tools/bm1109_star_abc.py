"""Coupled abc passive-braking model for a three-wire star with floating neutral."""
from __future__ import annotations
import math
from typing import Sequence
from tools.bm1109_braking import BemfTable, BrakingResult

_TWO_PI = 2.0 * math.pi


def _matrix3(a):
    if len(a) != 3 or any(len(row) != 3 for row in a):
        raise ValueError("inductance matrix must be 3x3")
    m = tuple(tuple(float(x) for x in row) for row in a)
    if not all(math.isfinite(x) for row in m for x in row):
        raise ValueError("inductance matrix must be finite")
    scale = max(abs(x) for row in m for x in row)
    if scale == 0:
        raise ValueError("inductance matrix must be positive definite")
    tol = max(1e-15, scale * 1e-10)
    for i in range(3):
        for j in range(3):
            if abs(m[i][j] - m[j][i]) > tol:
                raise ValueError("inductance matrix must be symmetric")
    # Cholesky test for strict positive definiteness.
    c = [[0.0]*3 for _ in range(3)]
    for i in range(3):
        for j in range(i+1):
            v = m[i][j] - math.fsum(c[i][k]*c[j][k] for k in range(j))
            if i == j:
                if v <= tol:
                    raise ValueError("inductance matrix must be positive definite")
                c[i][j] = math.sqrt(v)
            else:
                c[i][j] = v/c[j][j]
    return m


def _solve(a, b):
    n = len(b)
    x = [list(a[i]) + [float(b[i])] for i in range(n)]
    for j in range(n):
        p = max(range(j,n), key=lambda r: abs(x[r][j]))
        if abs(x[p][j]) < 1e-18:
            raise ValueError("singular KKT system")
        x[j],x[p] = x[p],x[j]
        d=x[j][j]
        for k in range(j,n+1): x[j][k] /= d
        for r in range(n):
            if r == j: continue
            f=x[r][j]
            for k in range(j,n+1): x[r][k] -= f*x[j][k]
    return tuple(x[i][n] for i in range(n))


def _derivative(i,e,r,l):
    # L*di/dt + vn*1 = e-R*i; 1^T*di/dt = 0.
    rhs=[e[k]-r[k]*i[k] for k in range(3)]
    kkt=[list(l[k])+[1.0] for k in range(3)]+[[1.0,1.0,1.0,0.0]]
    z=_solve(kkt,rhs+[0.0])
    return (z[0],z[1],z[2])


def _rk4(i,t,dt,period,rpm,table,r,l):
    def f(ts,state):
        angle=360.0*(ts % period)/period
        return _derivative(state,table.at_angle(angle,rpm),r,l)
    a=f(t,i)
    b=f(t+dt/2,tuple(i[k]+dt*a[k]/2 for k in range(3)))
    c=f(t+dt/2,tuple(i[k]+dt*b[k]/2 for k in range(3)))
    d=f(t+dt,tuple(i[k]+dt*c[k] for k in range(3)))
    return tuple(i[k]+dt*(a[k]+2*b[k]+2*c[k]+d[k])/6 for k in range(3))


def simulate_star_abc(table: BemfTable, rpm: float,
                      winding_resistance_ohm: Sequence[float],
                      load_resistance_ohm: Sequence[float],
                      inductance_matrix_h: Sequence[Sequence[float]], *,
                      steps_per_period: int | None = None,
                      max_periods: int = 100,
                      periodic_tolerance_a: float = 1e-7) -> BrakingResult:
    """Three-wire star, floating neutral, series phase loads and constant Labc."""
    if not math.isfinite(rpm) or rpm < 0:
        raise ValueError("rpm must be finite and non-negative")
    if len(winding_resistance_ohm)!=3 or len(load_resistance_ohm)!=3:
        raise ValueError("exactly three phase resistances are required")
    if any(not math.isfinite(x) or x<0 for x in (*winding_resistance_ohm,*load_resistance_ohm)):
        raise ValueError("resistances must be finite and non-negative")
    if not isinstance(max_periods,int) or max_periods<2 or not math.isfinite(periodic_tolerance_a) or periodic_tolerance_a<=0:
        raise ValueError("invalid periodic convergence settings")
    l=_matrix3(inductance_matrix_h)
    r=tuple(winding_resistance_ohm[k]+load_resistance_ohm[k] for k in range(3))
    if any(x<=0 for x in r): raise ValueError("each phase needs positive total resistance")
    if rpm==0:
        return BrakingResult(0.0,0.0,0.0,(0.0,)*3,(0.0,)*3,0.0,0.0,0.0,0,True,0.0,0.0)
    omega_m=_TWO_PI*rpm/60.0
    period=1.0/(table.pole_pairs*rpm/60.0)
    n=steps_per_period or max(512,table.samples_per_period*8)
    if not isinstance(n,int) or n<table.samples_per_period:
        raise ValueError("steps_per_period must be an integer >= BEMF sample count")
    dt=period/n
    current=(0.0,0.0,0.0)
    last=[]
    err=math.inf
    converged=False
    used=0
    for p in range(max_periods):
        start=current
        samples=[]
        for k in range(n):
            samples.append(current)
            current=_rk4(current,k*dt,dt,period,rpm,table,r,l)
        err=max(abs(current[k]-start[k]) for k in range(3))
        last=samples
        used=p+1
        if err<=periodic_tolerance_a:
            converged=True
            break
    pem=pcu=pload=0.0
    i2=[0.0]*3
    peak=[0.0]*3
    for k,state in enumerate(last):
        emf=table.at_angle(360.0*k/n,rpm)
        for j in range(3):
            v=state[j]
            pem+=emf[j]*v
            pcu+=winding_resistance_ohm[j]*v*v
            pload+=load_resistance_ohm[j]*v*v
            i2[j]+=v*v
            peak[j]=max(peak[j],abs(v))
    pem*=dt/period; pcu*=dt/period; pload*=dt/period
    rms=tuple(math.sqrt(v/n) for v in i2)
    eb=pem-pcu-pload
    pct=100.0*eb/pem if abs(pem)>1e-15 else 0.0
    return BrakingResult(rpm,pem/omega_m,pem,rms,tuple(peak),pcu,pload,err,used,converged,eb,pct)
