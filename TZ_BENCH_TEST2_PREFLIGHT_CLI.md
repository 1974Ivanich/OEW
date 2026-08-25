# ТЗ: CLI validator physical no-HV pre-flight Test №2

## Цель

Добавить `tools/bench_test2_preflight.py`: единый fail-closed CLI validator для pre-flight перед physical Test №2 на ПК-3. Он выпускает `preflight_summary.json` со всеми checks, фактическими evidence paths и verdict `PREFLIGHT=PASS|FAIL`.

Инструмент **никогда** не выполняет `mcarm`, `mapcap run`, `mapcap drain`, `f`, `mapcap build`, FOC, V/f, autotune, flash или любые команды DC-link. Он не является Test №2 executor. `PREFLIGHT=PASS` разрешает только запуск уже утверждённого automation command; это не Test №2 PASS и не Stage-A permit.

## Режимы

| Mode | Поведение | Hardware boundary |
|---|---|---|
| `--offline` | Проверяет campaign layout, `g0_check_summary.json`, required G0 evidence, explicit operator confirmations и optional saved UART transcript. Не открывает COM/USB/sigrok/ST-Link. | Default; безопасен на disconnected workstation. |
| `--port <MCU_UART_VCP>` | Открывает только заданный MCU UART VCP и выполняет allow-list observation commands. | Требует все четыре explicit confirmations; ничего не запускает/не прошивает. |

Real UART mode разрешён только при одновременных `--confirm-dc-link-disconnected`, `--confirm-pc4-zero`, `--confirm-sd-high`, `--confirm-sigrok-connected`. Эти flags являются operator attestations, не заменяют DMM/scope evidence.

## Входы

```powershell
py -3 tools\bench_test2_preflight.py `
  --campaign <campaign-root> `
  --offline `
  --confirm-dc-link-disconnected `
  --confirm-pc4-zero `
  --confirm-sd-high `
  --confirm-sigrok-connected
```

Real mode добавляет `--port`, `--baud` (default 115200), `--sigrok-cli` optional, `--scan-sigrok` optional и те же confirmations.

Campaign root должен быть отдельным от Git, существовать и быть writable. Required input files:

- `g0_approval.json`;
- `diagnostic_build_manifest.json`;
- `diagnostic_build.log`;
- `g0_check_summary.json` либо `g0/g0_check_summary.json`, ровно один;
- `metadata.json` (создаётся/обновляется только preflight tool в campaign);
- evidence directory writable.

Если `metadata.json` не существует, tool создаёт его только после успешной проверки campaign boundary; он никогда не изменяет G0 inputs. Output `preflight_summary.json` создаётся/заменяется atomically в campaign и включает input hashes, operator confirmations, commands/responses (real mode) и checks.

## Offline checks

1. Campaign root существует, не является symlink и не расположен в Git working tree.
2. Все required G0 files являются regular files, safe relative paths; G0 summary ровно один.
3. G0 summary — valid JSON object, `gate=HIL_TEST2_G0`, `verdict=PASS`.
4. `g0_approval.json` имеет `decision=APPROVED`, `allows_physical_nohv_execution=true`, `diagnostic_only=true`, all forbids Stage A/DC-link/FOC/V/f/autotune/production true.
5. Manifest имеет target `physical-nohv-diagnostic`, test `MAPCAP_TEST2`, complete required defines and `defines_complete=true`.
6. Operator confirmations all true. Missing evidence/field is FAIL.
7. Optional `--uart-transcript <path>` is parsed using the same strict response rules as real UART mode; it provides offline reproduction but not proof of a physical target identity.

## Real UART allow-list

Sequence is exactly `sysinfo`, `p?`, `pdump`, `a`, `c`, `enc`, `mapcap status`. Any transport error, timeout, blank response or extra/unrecognised command is FAIL. The command sequence is recorded.

| Command | PASS criterion |
|---|---|
| `sysinfo` | Nonempty response without CLI error marker; retained in summary. |
| `p?`, `pdump` | Each confirms `MOE=0` or `default_deny=1`; no expected PWM admission. |
| `a` | Reuses existing strict ADC parser; `raw_vbus <= 9`, I1/I2 fields available and not saturated (absolute `<32767`). |
| `c` | Reuses existing calibration parser; `@ADC:CAL:FAIL` absent and offsets present. |
| `enc` | Reuses existing encoder parser; `err=0`. |
| `mapcap status` | Reuses extended strict parser; `state=IDLE (0)`, `term=0`, `frames=0`, `dropped=0`, `avail=0`. |

`--scan-sigrok` may execute only `sigrok-cli --scan` after all offline/confirmation gates pass. It requires exit 0 and nonempty output but is a connectivity check only—not actual Test №2 capture/scope evidence.

## Verdict and exit codes

- `PREFLIGHT=PASS`, exit 0: all offline/operator/UART/sigrok-requested checks PASS.
- `PREFLIGHT=FAIL`, exit 2: any failure; no control or energising command is sent.
- All results include `kind=OFFLINE|UART|SIGROK|OPERATOR`, expected/actual/detail and timestamp.

## Tests and acceptance

Add deterministic tests for valid offline campaign; every G0/manifest/confirmation failure; transcript-positive and malformed/unsafe ADC/calibration/encoder/status/PWM cases; no `mcarm/run/f/build` in command sequence; real mode blocked without confirmations; optional sigrok scan command formation; atomic summary. Run targeted pytest, existing Test №2/G0/archive suites, `make`, `make test`, diff check, rebase/push/ls-remote. `make flash` prohibited: this software package must not touch physical hardware during development.
