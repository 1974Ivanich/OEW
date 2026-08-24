# OewCurrentMap v2 artifact writer

## Purpose

`MapArtifactWriter` is the host-side boundary between the measured characterization
pipeline and the STM32 runtime map loader. It does not perform characterization
itself and does not synthesize timing/reference evidence.

## Inputs

1. `OewMapIdentity` from the actual board configuration.
2. `OewMapProvenance` identifying the measured dataset and characterization tools.
3. Measured `CurrentReconEntry[sector][window]` from `MapMeasurement_SolveM`.
4. Measured `OewPwmRegion[sector][window]` from `MapRegionCertify`.
5. Explicitly qualified startup context.

All inputs must already have passed their respective qualification checks.

## Outputs

- `OewCurrentMap` revision 2, CRC protected and suitable for firmware validation.
- Human-readable JSON audit metadata. JSON is non-authoritative and is never
  consumed by firmware.

## Safety properties

- Identity is copied from the active board configuration; it is not inferred
  from nominal constants.
- Provenance is part of the CRC-covered map artifact.
- Revision 1 artifacts are not silently upgraded.
- Invalid/zero identity or provenance signatures are rejected by the writer.
- The writer never turns an unqualified measurement into a valid region.

## Deployment boundary

The writer is host-side. Runtime firmware remains responsible for CRC, revision,
identity, structural validation, commissioning interlock and atomic map load.

The next integration step is to add a CLI/file writer that calls this API after
`MapMeasurementAccumulator` -> `MapMeasurement_SolveM` -> `MapRegionCertify` and
emits `oew_map_v2.bin` plus `oew_map_v2.json`.
