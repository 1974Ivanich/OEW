# Bench session 27.08.2026 — Test №2 → Test №3 → Stage A (60 V)

Полная цепочка physical no-HV → energized на ПК-3. Source: `5bfd1bf58a1e5fba3c52ef0d64686ecba1ae2d78`
(приёмка `ai-bench/test3-diagnostic-contract-6defines`, контракт 6 defines).
Evidence-кампании — вне git: `C:\campaign_raw\`.

## Test №2 — ADC baseline (default-deny, no-HV) — **PASS**
- Кампания: `C:\campaign_raw\test2_adc_20260827T134524Z\`
- `sysinfo` CLK=170 МГц, identity NUCLEO-G474RE (ST-Link SN 004E00223133511237363734, COM4).
- PWM disabled: `p?` CCER=0/BDTR=7360 (MOE=0); `pdump` T1/T8 default-deny.
- `c` → offsets 2039/2068/0 (без FAIL); `a`×10 полные; VBUS no-HV статистический гейт PASS (медиана ≤9, max ≤200).

## Test №3 — controlled no-HV MapCapture — **automation PASS + TERMINAL_VERDICT PASS**
- Кампании: `test3_nohv_20260827T151058Z` (G0-валидация), `test3_nohv_20260827T151147Z` (physical run).
- G0: `G0-20260827-002` APPROVED (6-defines diagnostic image, firmware_sha256 `fcabd72b…872b8`).
- `mcarm=SYNT` → cap=1 rc=0 offsets_valid=1 (после контракта 6 defines; ранее BLOCKED:PROFILE).
- Terminal: `state=5, term=-12, detail=7 (VBUS_LOW), adc_status=7 (WINDOW_INVALID)`, zero frames/records, raw_vbus=5.
- sigrok: rc=0, `sigrok_digital.csv` 30.8 МБ; `bench_test2_rerun_verdict.py` → **TERMINAL_VERDICT=PASS**;
  scope принят оператором (`scope/scope_review.md`).

## Stage A — 60 V energize-only (production default-deny) — **PASS**
- Кампания: `C:\campaign_raw\stage_a_60v_20260827T151851Z\`
- Run1: при energize `FAULT_R=18 (PROTECT_FAULT_HARDWARE_BREAK)` — protection сработала fail-closed
  (PWM off, MOE=0, токов нет); классифицировано оператором как **штатная защита** (`event_classification.md`).
- Clear: `f` → «fault cleared» (VBUS в recovery-окне ≥8 В, SD high).
- Run2: **PASS** — VBUS raw median **596** (≈60 В), FAULT=0, PWM off, 69 сэмплов, латч не повторился.
- Пост-проверка: после снятия питания VBUS=0, FAULT=0, PWM off.
- Вывод: плата корректно измеряет 60 В; protection штатно реагирует на energize-транзиент.

## Границы / статус
- Control (FOC/V-f), старт двигателя, мапкап при энергии, >60 В — **запрещены**, требуют отдельного
  процесса (control admission, commissioning-образ, карта, A60-04/05/08).
- Плата возвращена на production default-deny; DC-link отключён; git чистый.
