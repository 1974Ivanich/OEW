# Test3 G0 enforcement patch specification

This document defines the minimum acceptance criteria for `ai-fix/test3-g0-enforcement` before any merge into `main`.

## Exact Test3 scope

The validator MUST fail closed unless all of these values match exactly:

- `target = physical-nohv-diagnostic-test3`
- `test = MAPCAP_TEST3`
- `allows_oew_host_test = false`
- `allows_physical_nohv_execution = false`
- `diagnostic_only = true`
- `forbids_stage_a = true`
- `forbids_production = true`
- `forbids_dc_link = true`
- `forbids_foc = true`
- `forbids_vf = true`
- `forbids_autotune = true`

## Build provenance

The validator MUST bind the approved G0 to the retained diagnostic build manifest and firmware:

`plan.source_sha == g0.source_sha == manifest.source_sha`

The manifest MUST be a retained regular file, identify the expected Test3 build, declare complete defines, and bind the retained firmware path/hash.

Required defines and forbidden defines MUST be checked against the manifest. Any define outside the required set MUST equal `g0.approved_extra_defines` exactly. Forbidden commissioning defines MUST be absent.

The firmware SHA-256 in the manifest and G0 MUST equal the SHA-256 of the retained firmware bytes.

## Regression requirements

Negative tests MUST cover every newly enforced scope field and every provenance failure mode, including missing manifest, source mismatch, incomplete/missing defines, forbidden define, unapproved extra define, and firmware hash mismatch.

The old seven-key Test3 scope MUST fail closed.

## Acceptance

Required checks:

- Python compilation succeeds.
- Full route regression suite passes.
- `git diff --check` is clean.
- No change is made to `main` by the remediation branch.
- Physical execution remains prohibited until a separate approved Test3 G0 is populated and reviewed.
