# Synthetic MapCapture profile

This test profile is **not board-qualified** and must never be used for an energise campaign.

It is compiled only when both `OEW_HOST_TEST=1` and `OEW_MAP_SYNTHETIC_PROFILE=1` are defined.
A normal firmware build, including commissioning builds, therefore retains the generic
fail-closed implementation.

The constants deliberately represent a synthetic configuration. They are not measurements
and must be replaced only by Stage-A bench data after review and approval. The host regression
proves only:

- exact request matching;
- rejection after changing one physical request field;
- rejection of unknown profile IDs;
- construction of all 12 qualification rows;
- default-deny behavior without the host-only defines.

No UART/user input can create or modify the synthetic request.