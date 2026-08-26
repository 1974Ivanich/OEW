# Transition: Test №2 ADC baseline → Test №3 controlled no-HV MapCapture

## Decision

The physical work is classified as follows.

| Identifier | Scope | Execution authority |
|---|---|---|
| **Test №2** | Baseline physical verification of measurement chain and ADC on ПК‑3 with default-deny firmware. | No MapCapture, G0 diagnostic flash, `mcarm`, `mapcap run`, FOC, V/f, autotune or DC-link. |
| **Test №3** | Future controlled no-HV MapCapture campaign, G0 and pre-flight evidence. | Blocked until a dedicated Test №3 G0 migration package, human approval and subsequent pre-flight PASS. |

This is a documentation and planning decision. It does not silently change a machine-readable contract already accepted into `main`.

## Legacy contract boundary

The current scripts/files named `bench_test2_*`, the G0 checker and the current physical automation encode `HIL_TEST2_G0` and `MAPCAP_TEST2`. They remain legacy identifiers until a separate reviewed migration updates them as one coherent package.

> A legacy `HIL_TEST2_G0=PASS` must not be renamed to Test №3 PASS. Conversely, the Test №3 template intentionally uses `HIL_TEST3_G0` / `MAPCAP_TEST3` and must fail under the legacy checker. This prevents a false approval caused by text-only renumbering.

## Published materials

| Material | Purpose |
|---|---|
| [TEST2_ADC_CHAIN_PC3_PLAN.md](TEST2_ADC_CHAIN_PC3_PLAN.md) | No-HV default-deny physical plan for Test №2 ADC/measurement-chain baseline. |
| [G0_CURRENT_STAGE_VERIFICATION_20260825.md](G0_CURRENT_STAGE_VERIFICATION_20260825.md) | Actual observed legacy offline G0 FAIL and missing-input evidence. |
| [templates/test3_nohv_campaign/](templates/test3_nohv_campaign/) | PENDING-only Test №3 campaign/G0 template. |

## Required next package before Test №3

A Test №3 migration package must define and test the new schemas, update all validator/profile/pre-flight/archive/repository references together, protect the same no-HV safety boundary and pass CI before any Test №3 G0 PASS can be claimed. It must include explicit migration review of every old `HIL_TEST2_G0`, `MAPCAP_TEST2`, `test2_nohv` and `bench_test2_*` reference. This documentation package does not perform that migration.

Until that package is accepted, diagnostic flash, `mcarm`, `mapcap run`, RealBoardProfile and automatic characterization remain **NO-GO**.
