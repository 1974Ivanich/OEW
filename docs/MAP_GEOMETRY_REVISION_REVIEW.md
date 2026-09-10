# MAP GEOMETRY REVISION REVIEW — PR #12

**Reviewer:** ai2 (Hermes)
**Date:** 2026-09-10
**PR:** ai4/map-region-geometry-revision
**Commit:** 16fadf0
**Files changed:** 13 files, +761/-680 lines

## Executive Summary

**RECOMMENDATION: APPROVE with conditions**

Web AI implementation of geometry bounds revision is technically sound and addresses the core problem identified in TZ_FOC_REGION_TOO_NARROW. The implementation includes proper safety measures (revision bump, wire format change, admission rules, interpolation harness). However, production PASS requires campaign rebuild and edge interpolation evidence.

## Review Findings

### 1. Geometry Predicates — **APPROVE**

**Implementation:**
- Sector predicates based on actual `vf_control.c` phase permutations
- Boundary tie rule: descending phase value with U < V < W
- Formal contract ensures exactly one owner for every nonzero balanced vector
- Runtime selector uses same rule as certifier

**Assessment:**
- Correctly addresses limitation of original draft predicates (`mu > 0 > mv > mw`)
- Deterministic boundary ownership is mathematically sound
- Matches existing firmware semantics in `vf_control.c`

**Approval:** ✅ Approved

### 2. Amplitude Zones — **CONDITIONAL APPROVE**

**Implementation:**
- Amplitude metric: `mod_q15 = max(abs(mu), abs(vv), abs(mw))`
- Window intervals: `W0: 6000 <= mod < 10000`, `W1: 10000 <= mod <= 14000`
- Window ownership: `mod=10000` belongs to W1 only (no overlap)
- Admission rejects `W0.max > W1.min`

**Assessment:**
- Radial metric is correct (independent of phase ordering)
- Window partitioning is sound (no overlap, deterministic ownership)
- **Condition:** Current BOAR campaign uses centres around Q15 8192, window-1 shift only 16 CCR → window-1 points ~9240 Q15, which is BELOW proposed W1.min=10000

**Approval:** ✅ Approved (conditional on campaign rebuild)

### 3. Artifact Representation — **APPROVE**

**Implementation:**
- Map revision: 2→3
- Wire size: 497→545 bytes
- `OewPwmRegion` stores: legacy bounds + `geometry_mod_min_q15` + `geometry_mod_max_q15` + `reserved=1`
- Statistical maps retain `reserved=0`
- v2 maps rejected by v3 loader

**Assessment:**
- Format revision is correct (prevents silent reinterpretation)
- Dual-mode representation is sound (legacy + geometry)
- Version bump is appropriate for contract change

**Approval:** ✅ Approved

### 4. Admission Rules — **APPROVE**

**Implementation:**
- Geometry mode: all 12 regions must be valid geometry regions
- All 12 reconstruction rows must be valid
- Positive modulation interval required
- Window overlap rejected
- Cross-sector rectangle overlap ignored (correct: predicates are disjoint)
- Startup vector must satisfy same sector/amplitude predicate
- Mixed geometry/statistical maps rejected

**Assessment:**
- Rules are comprehensive and fail-closed
- Correctly identifies geometry vs statistical modes
- Startup containment check is sound

**Approval:** ✅ Approved

### 5. Certification Rules — **APPROVE**

**Implementation:**
- `MapRegionCertifyForSectorWindow()` in geometry mode constructs radial interval
- Valid cells must satisfy sector predicate AND radial interval
- `MAP_CERT_VALID_OUTSIDE` for valid cells outside region
- Existing fail-closed statuses for invalid/untested inside

**Assessment:**
- Correctly implements geometry certification
- Fail-closed behavior preserved
- New status code is appropriate

**Approval:** ✅ Approved

### 6. Interpolation Evidence — **APPROVE (mechanism)**

**Implementation:**
- `map_geometry_interpolation_check.py` fits centre M with `y=Mx` model
- Evaluates predicted phase currents at edge samples against independent reference
- RMS edge error must not exceed `residual_rms_limit_ma`
- Harness deliberately does not manufacture safety evidence

**Assessment:**
- Mechanism is sound (same model as firmware characterization)
- Real campaign edge samples required for safety acceptance
- Synthetic test proves gate mechanics only

**Approval:** ✅ Approved (mechanism only, real evidence required)

### 7. Backward Compatibility — **APPROVE**

**Implementation:**
- `use_geometry_bounds=false` retains statistical path
- Existing statistical campaigns can be rebuilt without geometry
- v2 maps rejected by v3 loader (prevents silent misinterpretation)

**Assessment:**
- Backward compatibility preserved for statistical mode
- v2/v3 separation is correct safety measure

**Approval:** ✅ Approved

### 8. BOAR Campaign Blocker — **CONFIRMED**

**Documented in PR:**
- Current BOAR capture profile: centres around Q15 8192
- Window-1 shift: 16 CCR → window-1 points ~9240 Q15
- Proposed W1.min=10000 Q15: window-1 points are BELOW this bound
- Conclusion: new BOAR artifact cannot be authorized without campaign rebuild

**Assessment:**
- Analysis is correct
- Campaign rebuild is required before production PASS

**Approval:** ✅ Confirmed blocker (campaign rebuild required)

## Code Quality Assessment

### Files Changed
- `src/current_map_selector.c/.h` — core geometry logic ✅
- `src/map_region_certifier.c` — geometry certification ✅
- `src/map_candidate.c` — admission logic ✅
- `src/map_artifact_decoder.c/.h` — v3 decoder ✅
- `src/map_artifact_writer.c/.h` — v3 encoder ✅
- `tests/map_region_geometry_test.c` — geometry tests ✅
- `tools/map_geometry_interpolation_check.py` — interpolation harness ✅
- `tests/test_map_geometry_interpolation.py` — interpolation gate tests ✅
- `.github/workflows/map-geometry-revision.yml` — CI workflow ✅
- `docs/TZ_MAP_REGION_GEOMETRY_REVISION.md` — formal specification ✅

### Code Review Comments
- No obvious bugs in geometry sector logic
- Admission rules are comprehensive
- Wire format change is properly versioned
- Tests appear to cover key cases

## Conditions for Production PASS

1. ✅ **Geometry predicates** — Approved
2. ✅ **Amplitude zones** — Approved (conditional: campaign rebuild)
3. ✅ **Artifact representation** — Approved
4. ✅ **Admission rules** — Approved
5. ✅ **Certification rules** — Approved
6. ⏳ **Interpolation evidence** — Mechanism approved, real evidence required
7. ✅ **Backward compatibility** — Approved
8. ⏳ **Campaign rebuild** — Required (BOAR campaign does not fit proposed zones)
9. ⏳ **Stend verification** — Required after campaign rebuild

## Final Recommendation

**APPROVE PR #12 for merge into main** with the understanding that:

1. Geometry bounds revision is technically sound and addresses FOC region problem
2. Production PASS requires:
   - Campaign rebuild with new amplitude zones (or adjusted zones fitting BOAR data)
   - Edge interpolation evidence from real campaign samples
   - Stend verification (mapload + FOC_START + motor run)
3. Map revision bump (2→3) and wire format change (497→545 B) are appropriate safety measures

## Next Steps After Merge

1. Merge ai4/map-region-geometry-revision into main
2. Adjust amplitude zones to fit BOAR campaign data (if needed)
3. Rebuild BOAR 60V campaign with geometry mode
4. Generate edge interpolation evidence using `map_geometry_interpolation_check.py`
5. Verify edge error < `residual_rms_limit_ma`
6. Stend verification: mapload + FOC_START + motor run
7. Update TZ_FOC_REGION_TOO_NARROW.md with verification results
