# TZ_MAP_REGION_GEOMETRY_REVISION

## Status
Implementation branch: `ai4/map-region-geometry-revision`.
Safety-owner approval is still required before a geometry map may be deployed.

## Formal contract revision

The original draft predicates using `mu > 0 > mv > mw` do not cover the full
zero-sequence-free phase plane. The implementation therefore uses the exact six
phase permutations already used by `vf_control.c`:

| Sector | Strict interior ordering | Boundary tie rule |
|---|---|---|
| 0 | `mu > mv > mw` | `mu >= mv >= mw` |
| 1 | `mu > mw > mv` | `mu >= mw > mv` |
| 2 | `mv > mu > mw` | `mv > mu >= mw` |
| 3 | `mv > mw > mu` | `mv >= mw > mu` |
| 4 | `mw > mu > mv` | `mw > mu >= mv` |
| 5 | `mw > mv > mu` | `mw >= mv > mu` |

The command must satisfy:

```text
mu + mv + mw = 0
```

and `(mu,mv,mw) != (0,0,0)`.

The equivalent deterministic rule is descending phase value with ties resolved
by phase label order `U < V < W`. This creates exactly one owner for every
nonzero balanced vector, including equal-phase boundaries. The runtime selector
uses the same rule as the certifier.

## Amplitude metric

The radial modulation coordinate is:

```text
mod_q15 = max(abs(mu), abs(mv), abs(mw))
```

All arithmetic is performed in a widened signed type before absolute value.
This metric is independent of the arbitrary phase that is largest in a sector
and therefore cannot be represented safely by a single phase bound.

## Window ownership

Window intervals are radial and are not priority rectangles:

```text
W0: mod_min_0 <= mod < mod_max_0
W1: mod_min_1 <= mod <= mod_max_1
```

For the proposed default zones:

```text
W0: 6000 <= mod < 10000
W1: 10000 <= mod <= 14000
```

Therefore `mod=10000` belongs to W1 only. Admission rejects any configuration
where `W0.max > W1.min`.

The amplitude values are still a safety-owner decision. They are not evidence
that the present BOAR campaign qualifies for those zones.

## Artifact representation

Geometry cannot be represented by six independent phase rectangles. `OewPwmRegion`
therefore stores:

- the legacy six Q15 bounds as a storage envelope;
- `geometry_mod_min_q15`;
- `geometry_mod_max_q15`;
- `reserved=1` as the geometry-mode discriminator.

Statistical maps retain `reserved=0` and the legacy per-phase bounds.

The binary artifact revision is bumped from v2 to v3 and the canonical wire size
from 497 to 545 bytes. This is deliberate: changing the meaning of a previously
reserved byte without a format revision would allow an old decoder/loader to
misinterpret a safety-critical map.

## Admission rules

For geometry maps:

1. all 12 regions must be valid geometry regions;
2. all 12 reconstruction rows must be valid;
3. every region must have a valid positive modulation interval;
4. the two windows of every sector must not overlap;
5. cross-sector rectangle overlap is ignored because the formal sector
   predicates are disjoint by construction and own all boundaries deterministically;
6. startup vector must satisfy the same sector and amplitude predicate;
7. a mixed geometry/statistical map is rejected.

For statistical maps, the existing rectangle-overlap and rectangle-containment
rules remain unchanged.

## Certification rules

`MapRegionCertifyForSectorWindow()` in geometry mode constructs the radial
interval and sector semantics rather than min/max statistics. Every certified
valid cell must satisfy both the sector predicate and radial interval. A valid
cell outside the region returns `MAP_CERT_VALID_OUTSIDE`; an invalid or untested
cell inside returns the existing fail-closed statuses.

## Interpolation evidence

A row-centre reconstruction matrix is not considered physically proven for the
entire geometric wedge merely because the centre cell passed the solver.

`tools/map_geometry_interpolation_check.py` fits the centre matrix using the same
no-intercept `y=Mx` model and evaluates its predicted phase currents at supplied
edge samples against the independent phase-current reference. The RMS edge error
must not exceed `residual_rms_limit_ma`.

The harness deliberately does not manufacture safety evidence. A synthetic test
proves the gate mechanics only. A real BOAR acceptance requires real edge samples
from the campaign. If the measured edge error exceeds the limit, the campaign
must add certified interior points or reduce the radial window.

## Backward compatibility

`use_geometry_bounds=false` retains the statistical certifier path. Existing
statistical campaigns can be rebuilt without geometry semantics. Binary v2 maps
are intentionally rejected by the v3 loader; this prevents silent reinterpretation
of the old wire contract.

## Known BOAR campaign blocker

The current BOAR capture profile uses centres around Q15 8192 and a window-1 shift
of only 16 CCR ticks. The proposed W1 lower bound of 10000 Q15 therefore cannot be
claimed against the existing campaign without rebuilding the campaign. No new
BOAR artifact is authorized by this branch.
