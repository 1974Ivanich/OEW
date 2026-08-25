# Plan Test №2 — базовая физическая проверка измерительной цепи и ADC на ПК‑3

**Классификация после перенумерации:** Test №2 проверяет baseline физической измерительной цепи при no-HV. Controlled MapCapture campaign с G0 относится к Test №3 и **не входит** в этот план.

> Test №2 не включает `mcarm`, `mapcap run`, `mapcap build`, `f`, FOC, V/f, autotune, DC-link или RealBoardProfile. Его PASS подтверждает только baseline ADC/measurement-chain evidence; он не разрешает Test №3, Stage A или characterization автоматически.

## 1. Цель и граница доказательства

Цель Test №2 — подтвердить на полном ПК‑3 стенде, что default-deny firmware действительно запущена на нужном Nucleo, что датчики/aux wiring дают живой и калибруемый ADC baseline, и что при физически отключённом DC-link VBUS остаётся в low/no-HV области. Все результаты фиксируются в отдельной campaign directory вне Git.

Test №2 подтверждает **нулевой baseline и целостность signal chain**. Без утверждённого эталонного источника он не подтверждает gain/linearity абсолютной калибровки тока или VBUS. Не добавляйте внешний VBUS на PC4 и не подавайте DC-link, чтобы искусственно расширить этот тест: это будет отдельное ТЗ и отдельный риск-анализ.

## 2. Required bench configuration

| Элемент | Required state | Evidence |
|---|---|---|
| Source revision | Accepted `main`, green CI, clean worktree; baseline default-deny build | SHA, CI run, build log |
| Nucleo/ST-Link | Правильная board identity и flash verify | ST-Link serial, programmer log |
| STEVAL and sensors | Полный approved harness подключён к штатным I1/I2/VBUS paths | Wiring/photo/observer confirmation |
| Aux supply | Отдельное low-voltage 3.3 V aux supply; не DC-link | DMM voltage and source identity |
| DC-link | Обе шины физически disconnected; DMM each `<1 V` | Two DMM readings/time |
| PC4 | Нет внешнего VBUS source/imitator | Witness confirmation; optional approved measurement point |
| SD1/SD2 | Required safe state, no active fault | Approved observation |
| UART | Actual MCU VCP, not assumed COM number | `sysinfo` response |
| Capture | Continuous UART log to campaign | `uart/test2_adc_uart.log` |

## 3. Hard NO-GO before UART observations

Do not flash or start the session if any row below is not PASS.

| ID | Gate | PASS criterion | Evidence / initials |
|---|---|---|---|
| T2-G0 | No-HV boundary | DC-link rails both `<1 V` and physically disconnected | ______ |
| T2-G1 | PC4 | No external VBUS injection/imitator | ______ |
| T2-G2 | Aux/sensors | Separate aux supply and full approved sensor harness | ______ |
| T2-G3 | Firmware baseline | Default-deny `main` build only; no Test №3 diagnostic flags | SHA/defines: ______ |
| T2-G4 | Flash identity | Verified flash only after T2-G0…G3 PASS | Programmer log: ______ |
| T2-G5 | Emergency action | Operator can remove aux according to local procedure | ______ |
| T2-G6 | Command boundary | Operator confirms no MapCapture/FOC/V/f/autotune commands will be issued | ______ |

A Test №3 `g0_approval.json` is **not** an input to Test №2. Its PENDING state neither authorizes nor blocks the default-deny ADC baseline; it blocks only Test №3 diagnostic execution.

## 4. Campaign layout

Create a new directory outside Git before any UART command.

```text
D:\campaign_raw\test2_adc_YYYYMMDDTHHMMSSZ\
├── identity/
│   ├── source_sha.txt
│   ├── build.log
│   └── flash_verify.log
├── dmm/
│   └── nohv_and_aux_measurements.md
├── uart/
│   ├── test2_adc_uart.log
│   └── adc_samples.csv
├── observations/
│   ├── wiring_check.md
│   └── operator_decision.md
└── summary/
    └── test2_adc_summary.md
```

The log must preserve boot/reset output and every TX/RX line. Do not clear the terminal buffer or copy only successful lines.

## 5. Controlled execution sequence

The following is an operator procedure. It is intentionally shorter than Test №3 and contains only non-energising observations.

| Step | Operator action | Expected result | Record |
|---:|---|---|---|
| 1 | Measure both DC-link rails with DMM, then apply only approved aux supply. | Rails `<1 V`; aux reading recorded. | DMM worksheet |
| 2 | Build default-deny `main` and flash only after hard gates PASS. | Programmer verify/reset succeeds. | Build and flash logs |
| 3 | Open actual MCU UART VCP and begin continuous capture. Send `sysinfo`. | Expected firmware responds; no stale/unknown command. | UART log |
| 4 | Send `p?` and `pdump`. | PWM disabled/`MOE=0` or default-deny state; no unexpected activity. | UART log, optional observation |
| 5 | Send `c` once. | Calibration contains `offset_i1`, `offset_i2`, `offset_ires`; no calibration failure. | UART log and sample table |
| 6 | Send `a` ten times at stable aux/no-HV conditions; preserve all raw lines. | I1/I2/Ires and VBUS fields present for every sample. | `adc_samples.csv` |
| 7 | Stop at baseline evidence. Do **not** arm or run MapCapture. | No `mcarm`, `mapcap run`, `mapcap build`, `f`, FOC, V/f or autotune in UART log. | Command audit |

## 6. Acceptance criteria

| Domain | PASS criterion | FAIL / BLOCKED condition |
|---|---|---|
| Firmware identity | `sysinfo` responds after verified default-deny flash; source/build/flash records are linked. | Unknown/stale response, unverifiable flash or diagnostic Test №3 image. |
| PWM default-deny | `p?` and `pdump` show PWM disabled; no unexpected board activity. | PWM admission/MOE active or ambiguous output. |
| Calibration | `c` gives all three offsets and no failure marker. | Missing offset, `@ADC:CAL:FAIL`, timeout or parse ambiguity. |
| ADC completeness | All ten `a` samples have I1, I2, Ires and VBUS fields. | Missing field, malformed sample, UART timeout or stale text. |
| No-HV VBUS | No external PC4 stimulus; raw VBUS is within the approved no-HV baseline (`≤9` raw counts for current Test №3 contract, recorded here as an observation). | Raw VBUS above baseline, DC-link not `<1 V`, or external stimulus present. |
| Current baseline | I1/I2/Ires do not report ADC rails/saturation; record min/max/mean/peak-to-peak. Human review confirms values are plausible for zero-current, powered sensors. | Rail codes, implausible jump, no calibration or unexplained nonzero/saturation. |
| Scope | UART command audit contains only `sysinfo`, `p?`, `pdump`, `c`, ten `a` observations. | Any energising/control command; terminate Test №2. |

For raw current codes, record facts instead of inventing a new unapproved acceptance tolerance. The historical nominal expectation is approximately mid-scale (`~2048`) after offset calibration, but the observed board-specific offsets, min/max and noise will be the actual input to later profile work. A constant sample is not automatically a pass or failure; it must be reviewed together with UART freshness, calibration output and sensor wiring.

## 7. Results worksheet

| Sample | UTC | I1 raw | I2 raw | Ires raw | VBUS raw | UART response complete | Notes |
|---:|---|---:|---:|---:|---:|---|---|
| 1 | | | | | | ☐ | |
| 2 | | | | | | ☐ | |
| 3 | | | | | | ☐ | |
| 4 | | | | | | ☐ | |
| 5 | | | | | | ☐ | |
| 6 | | | | | | ☐ | |
| 7 | | | | | | ☐ | |
| 8 | | | | | | ☐ | |
| 9 | | | | | | ☐ | |
| 10 | | | | | | ☐ | |

| Metric | I1 | I2 | Ires | VBUS |
|---|---:|---:|---:|---:|
| Min | | | | |
| Max | | | | |
| Mean | | | | |
| Peak-to-peak | | | | |

## 8. Verdict and transition

| Verdict | Meaning | Next action |
|---|---|---|
| **Test №2 PASS** | Default-deny identity, no-HV boundary, calibration and baseline ADC evidence are complete and accepted. | Freeze campaign evidence. Prepare a separate reviewed migration from legacy Test №2 G0 terminology to Test №3 G0; do not yet run Test №3. |
| **Test №2 FAIL** | Any hard gate, calibration, ADC, VBUS or command-boundary criterion failed. | Stop. Preserve evidence; open corrective work. No Test №3 G0, diagnostic flash or characterization. |
| **Test №2 BLOCKED** | Full harness, DMM/aux, verified baseline build or actual MCU UART VCP unavailable. | Do not substitute simulation or partial Nucleo setup for physical evidence. |

> Even a Test №2 PASS does not fill RealBoardProfile and does not launch automatic OEW characterization. RealBoardProfile may use only measured identity/timing data collected after the Test №3 migration, Test №3 G0 PASS and controlled no-HV pre-flight.

## References

[1]: `docs/BENCH_FIRST_SESSION.md` — default-deny, no-HV, calibration and ADC baseline guidance.

[2]: `tools/bench_test2_g0_check.md` — legacy Test №2 G0 contract; it is deliberately not reused as Test №3 evidence without migration.

[3]: `tools/bench_test2_capture.md` — controlled MapCapture automation boundary, now classified as Test №3 after migration.
