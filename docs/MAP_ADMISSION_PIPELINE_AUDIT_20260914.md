# Audit existing OEW map admission/certification pipeline — 2026-09-14

**Baseline:** `origin/main` / `2069d6728473c3d039f58fb078bfefb1d2baf3ea`
**Scope:** read-only audit; no firmware behaviour is changed by this document.

## 1. Executive verdict

**Pipeline audit: CONDITIONALLY PASS for artifact integrity + geometric certification; BLOCKED for physical qualification.**

The current repository has a real fail-closed map path, a canonical wire artifact, firmware-side admission, reconstruction validation, region validation, and geometry certification. The L3 documentation explicitly states that the builder is a coverage collector and does not derive reconstruction coefficients from measured ADC values; coefficients and regions come from a reviewed board-specific qualification profile. That is the correct architectural separation.

However, the current pipeline does **not** establish physical current-sensor qualification by itself. In particular, a campaign can pass structural/raw/evidence checks while the reference currents used by the solver are not independently demonstrated to be independent of the ADC-derived quantities. Therefore `MAP_READY` is not evidence that the physical observation matrix has been qualified.

## 2. Gate audit

| Gate | Current implementation | Verdict |
|---|---|---|
| G0 — artifact integrity | 497-byte canonical wire format; decode/CRC path; identity/provenance checks in admission | PASS |
| G1 — semantic/geometry | sector/window geometry certification and runtime selection exist; deterministic windows are documented | PASS for software contract/tests; physical boundary evidence still separate |
| G2 — sampling feasibility | capture records retain ADC/PWM/timing evidence and validator requires settled ADC/scope qualification/margin | CONDITIONALLY PASS; this proves capture evidence exists, not yet that all production control apertures are physically accurate |
| G3 — physical qualification | solver compares `idc1/idc2` to `reference` and can report fit/holdout/KCL/determinant metrics | **BLOCKED as physical proof** until reference provenance is independently established |

## 3. Existing pipeline that is already correct

The documented L3 path is:

```text
mapcap arm/run
  -> MapCaptureRecord ring
  -> MapBuilder_AddRecord
  -> MapBuilder_Finalize
  -> CurrentMap_LoadMeasured
  -> MAP_READY
```

The builder deliberately does not calculate coefficients from capture data. It copies the reviewed `CurrentReconEntry` and `OewPwmRegion` from `MapBuilderQualification`. This prevents arbitrary UART data from becoming a control map, but it also means the physical qualification of that reviewed profile must be demonstrated separately.

The firmware admission path also has the intended fail-closed preconditions: no active capture/FOC/V-f/autotune, PWM disabled, ADC injection not armed, no protection fault, live identity match, canonical candidate validation, reconstruction validation, region/startup validation, and CRC.

## 4. Critical qualification gap: independence of the reference

The dataset contract calls `ref_u_ma/ref_v_ma/ref_w_ma` a scope reference. That is sufficient only if the provenance chain proves that these values came from an electrically independent measurement path.

For physical qualification, the following must be prohibited:

```text
ADC idc1/idc2
   -> conversion/reconstruction
   -> ref_u/ref_v/ref_w
   -> OLS fit
   -> "validation"
```

That loop can produce excellent residuals without proving the ADC observation matrix.

The required physical model is:

```text
y_ADC = H * i_phase + b + n
```

where `i_phase` is measured by an independent reference instrument/path. The qualification result must identify `H`, offset/noise characteristics, and compare the identified matrix against the firmware reconstruction/current-map model.

## 5. Existing solver: what it proves and what it does not

`MapMeasurement_SolveM()` fits a 2x2 matrix from `capture.frame.idc1_ma/idc2_ma` to selected phase references and checks determinant, conditioning, residual, holdout, bias and KCL metrics.

These are valid numerical tests **conditional on reference independence**. They are not a substitute for demonstrating reference independence. In particular, a KCL-consistent reference generated from the same two ADC channels is not an independent physical observation.

## 6. Admission atomicity finding

There is one software-contract issue to carry as a separate implementation task, not to mix into TZ-02 physical qualification.

`MapCommissioning_LoadMeasured()` correctly performs all commissioning preconditions before calling the loader. However, `CurrentMap_LoadMeasured()` currently calls `CurrentMap_Reset()` **before** validating the candidate. Therefore, if a previous active map exists and the caller reaches the loader with PWM/ADC/FOC/etc. inactive, a bad replacement candidate can clear the previously valid map before returning `false`.

This conflicts with the desired replacement invariant:

```text
candidate FAIL
    => previous active map + readiness remain unchanged
```

It does not invalidate the current empty-map admission tests, but it is a real replacement/atomicity defect and should be fixed under a separate small admission-safety task before relying on hot replacement semantics.

**Status (14.09.2026): CLOSED.** Implemented in `2b2efdd`, accepted into `main` (`c494338`). The accepted replacement semantics are: a rejected candidate preserves the previously active map **only while its identity still matches the live identity**; on identity drift the map is invalidated (`CurrentMap_Reset()` + refusal, fail-closed). Covered by regressions R1/R2/R3/R3b in `tests/current_map_selector_test.c`. This fix is admission-only: PROTECT/ADC/PWM/FOC/map geometry are untouched.

## 7. Final audit status

```text
G0 artifact integrity       PASS
G1 geometry contract        PASS (software); physical boundary evidence separate
G2 sampling feasibility     CONDITIONALLY PASS
G3 physical map qualification BLOCKED

Overall current map physical qualification: BLOCKED

P0/P1 representative-row bench: READY TO EXECUTE (no physical PASS is claimed)
```

The next task is therefore not another geometry refactor. It is a minimal physical campaign contract with mandatory independent reference provenance.
