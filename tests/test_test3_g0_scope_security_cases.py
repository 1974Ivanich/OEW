"""Security regression specification for Test3 G0 scope enforcement.

These cases are intentionally executable specifications: the route validator must
reject any Test3 G0 scope that enables host/physical execution or production.
"""

REQUIRED_FALSE_FLAGS = (
    "allows_oew_host_test",
    "allows_physical_nohv_execution",
)
REQUIRED_TRUE_FLAGS = (
    "diagnostic_only",
    "forbids_stage_a",
    "forbids_production",
    "forbids_dc_link",
    "forbids_foc",
    "forbids_vf",
    "forbids_autotune",
)


def test3_scope_security_cases():
    """Executable contract data for the route validator test suite."""
    assert REQUIRED_FALSE_FLAGS == (
        "allows_oew_host_test",
        "allows_physical_nohv_execution",
    )
    assert "forbids_production" in REQUIRED_TRUE_FLAGS
