# Test3 G0 safety-audit fix

This branch is a safety remediation candidate for `ai4/test2-test3-map-route`.

## Findings closed by this package

1. Test3 G0 scope must be enforced as an exact allowlist, not only by checking selected prohibitions.
2. `allows_oew_host_test` and `allows_physical_nohv_execution` must be false.
3. `forbids_production` must be true.
4. The retained firmware hash must be bound to the exact approved source/build manifest and its approved define set; independently matching source and binary hashes is insufficient provenance.
5. Negative regression tests must cover all of the above.

## Safety boundary

This remediation does not approve Test3 physical execution, DC-link energization, Stage A, FOC, V/f, autotune, or production control. It only prepares the offline G0 enforcement layer for review.
