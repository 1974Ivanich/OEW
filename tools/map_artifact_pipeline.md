# L3 map artifact pipeline (host)

## Purpose

`MapArtifactPipeline` is the host-side orchestrator that connects the four
already-qualified stages of the L3 characterization chain:

```text
MapMeasurementAccumulator  (per row, sector/window)
        | 8..32 accepted samples per row
MapMeasurement_SolveM      (mA-domain OLS -> CurrentReconEntry[6][2])
        | residual/holdout/condition qualification
MapRegionCertify           (tested grid cells -> OewPwmRegion[6][2])
        | min-valid-cells / margin qualification
MapArtifactWriter_Build    (identity + provenance + startup + CRC)
        |
oew_map_v2.bin (497-byte canonical wire) + oew_map_v2.json (audit)
```

The pipeline is **host-only** and is deliberately not part of firmware
`C_SOURCES`. Runtime firmware keeps the loader contract: CRC, revision,
full identity, structural validation and atomic map load.

## Fail-closed semantics

Every one of the 12 rows must pass all three stages. Any failure — not
enough samples, noisy row, singular/ill-conditioned M, uncertified region,
bad dataset structure — aborts the run and **no artifact is produced**.
`MapPipelineReport` carries the failing sector/window and the stage status.

## Building

```bash
make -f tools/map_artifact_writer_test.mk map-artifact-pipeline-test  # regression
make -f tools/map_artifact_writer_test.mk map-artifact-cli            # CLI binary
make -f tools/map_artifact_writer_test.mk map-artifact-e2e            # CLI on demo dataset
```

The CLI binary is `tools/map_artifact_pipeline_cli.exe` (host gcc).

## CLI usage

```bash
tools/map_artifact_pipeline_cli.exe <dataset.txt> [out_dir]
```

Outputs (default out_dir = `.`):

- `oew_map_v2.bin` — canonical v2 wire artifact, `OEW_CURRENT_MAP_WIRE_SIZE`
  (497) bytes, CRC-protected, loadable by `CurrentMap_LoadMeasured`.
- `oew_map_v2.json` — non-authoritative human-readable audit metadata.

Exit codes: `0` = artifact written; `1` = pipeline/encoder failure (message
with failing row); `2` = dataset parse/usage error.

## Dataset format (plain text)

One command per line, `#` comments and blank lines ignored. Integers accept
decimal or `0x` hex. Values are `key=value` tokens, order within a line free.

```text
identity board=7 pwm=20000 arr=8499 trigger=0x4F455731 toff=0 dt=85
         clk=42500000 smp=1281 res=0 acs=0x11223344 ccs=0x55667788
provenance cid=0x01020304 dcrc=0xA1B2C3D4 tb=0x20260820 qr=2 sr=4 cr=2
startup sector=2 window=1 hold=25 mu=-1234 mv=2345 mw=-3456
accumq  min=8  mad=1000 kcl=100 margin=1
solverq min=8 holdout=2 rms=1000 max=2000 bias=1000 hrms=1000
        kclrms=100 cond=100000 det=1 diag=100
regionq min=4 guard=1 margin=3 use_geometry=1 w0_mod_min=6000 w0_mod_max=10000 w1_mod_min=10000 w1_mod_max=14000
row sector=0 window=0 phase_a=0 phase_b=1
sample seq=1 idc1=1000 idc2=2000 ict=0 vbus=12000
       refu=4000 refv=5000 refw=-9000 margin=4 settled=1 scope=1
cell mu=0 mv=0 mw=0 margin=5 status=1
```

| Command | Meaning |
|---|---|
| `identity` | live board/ADC identity captured at measure time (all 11 fields of `OewMapIdentity`) |
| `provenance` | campaign provenance (6 fields of `OewMapProvenance`) |
| `startup` | qualified startup context (sector/window/hold/mu/mv/mw) |
| `accumq` | accumulator qualification (min samples, MAD, KCL, margin) |
| `solverq` | solver qualification (holdout, residual/bias/holdout/KCL limits, condition, determinant, diagonal) |
| `regionq` | region certifier qualification; `use_geometry=0` keeps statistical bounds, while `use_geometry=1` uses six SVPWM wedges and the two modulation windows. Window limits are optional and default to 6000..10000 and 10000..14000 Q15. |
| `row` | starts a row; sector/window 0..5 / 0..1, phase_a/phase_b shunt wiring |
| `sample` | one measurement: injected capture (idc1/idc2/ict/vbus), scope phase reference, timing evidence |
| `cell` | one tested modulation grid cell for the current row (status 0=UNTESTED,1=VALID,2=INVALID) |

Constraints enforced by the CLI:

- all 12 rows must appear with at least one sample and one cell;
- `phase_a`/`phase_b` must be identical across rows (hardware wiring is fixed);
- sample `seq` must be unique within a row; capture PWM identity fields are
  taken from the manifest derived from `identity`;
- manifest `source` is always `MAP_REFERENCE_SOURCE_SCOPE` for this pipeline.

A synthetic working example lives in
[`tools/map_artifact_pipeline_demo.txt`](map_artifact_pipeline_demo.txt).
