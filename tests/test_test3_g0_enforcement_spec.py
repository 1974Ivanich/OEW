"""Static acceptance checks for the Test3 G0 enforcement specification.

These tests intentionally do not execute hardware or commissioning commands.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs" / "TEST3_G0_ENFORCEMENT_PATCH_SPEC.md"


def test_test3_g0_spec_exists_and_has_exact_scope():
    text = SPEC.read_text(encoding="utf-8")
    required = [
        "target = physical-nohv-diagnostic-test3",
        "test = MAPCAP_TEST3",
        "allows_oew_host_test = false",
        "allows_physical_nohv_execution = false",
        "diagnostic_only = true",
        "forbids_stage_a = true",
        "forbids_production = true",
        "forbids_dc_link = true",
        "forbids_foc = true",
        "forbids_vf = true",
        "forbids_autotune = true",
    ]
    for item in required:
        assert item in text


def test_test3_g0_spec_requires_closed_provenance():
    text = SPEC.read_text(encoding="utf-8")
    for item in (
        "plan.source_sha == g0.source_sha == manifest.source_sha",
        "approved_extra_defines",
        "Forbidden commissioning defines MUST be absent",
        "SHA-256 of the retained firmware bytes",
    ):
        assert item in text
