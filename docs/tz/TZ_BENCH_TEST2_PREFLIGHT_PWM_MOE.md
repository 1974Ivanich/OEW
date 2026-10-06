# ТЗ: preflight — распознавание выключенного PWM по биту MOE в BDTR

Статус: черновик пакета `ai4/bench-test2-preflight-pwm-moe`

## 1. Проблема

Preflight Test №2 (`tools/bench_test2_preflight.py`) проверяет «PWM выключен» по
текстовым маркерам `MOE=0` или `default_deny=1` в ответах `p?`/`pdump`
(`PWM_OFF_RE`). Реальная прошивка (diagnostic-образ, `src/cli.c`) таких маркеров
не эмитит:

```
p?     -> @PWM:CR1=224:CCER=0:BDTR=7360:CNT=0
pdump  -> @PWM:FULL:SYS=170000000:CFGR=0x0000000F:T1:PSC=16:ARR=999:CCR=0,0,0:
          BDTR=0x00001CC0:CCER=0x00000000:CR1=0x000000E0:CNT=0:T8:...
```

Проверено на стенде: `BDTR=0x1CC0` (дес. 7360) — бит MOE (TIM_BDTR bit 15, 0x8000)
**сброшен**, `CCER=0`, `CCR=0` → PWM фактически выключен (default-deny). Но preflight
fail-closed падает на `uart.pwm_off.*`, не понимая формат.

## 2. Цель

Научить preflight доказывать «PWM выключен» по реальному формату прошивки:
- сохранить поддержку прежних маркеров `MOE=0` / `default_deny=1` (совместимость
  с транскриптами/тестами);
- добавить разбор `BDTR=...` (десятичное и `0x`-шестнадцатеричное) для обоих
  таймеров (T1/T8 в `pdump`): PWM off ⇔ **во всех** BDTR бит MOE (0x8000) = 0.

## 3. Контракт

| Условие | Результат |
|---|---|
| `MOE=0` (текстовый маркер) | off |
| `default_deny=1` (текстовый маркер) | off |
| все `BDTR` в ответе имеют `(BDTR & 0x8000) == 0` | off |
| нет маркеров и нет ни одного `BDTR` | **не доказано** → FAIL |
| хотя бы один `BDTR` имеет `(BDTR & 0x8000) != 0` | **не выключен** → FAIL |
| `MOE=1` (текстовый) | FAIL |

Fail-closed: отсутствие доказательств = FAIL.

## 4. Объём изменений

Host-only. **Прошивка, `.ioc`, safety-модули не меняются.**

| Файл | Изменение |
|---|---|
| `tools/bench_test2_preflight.py` | заменить `PWM_OFF_RE` на функцию `pwm_off_evidence(response)`; использовать её в `validate_uart_responses` |
| `tests/test_bench_test2_preflight.py` | тесты реальных форматов: `BDTR=7360`, `BDTR=0x00001CC0` → PASS; `BDTR=0x00009CC0` (MOE=1) → FAIL; без BDTR/маркеров → FAIL; прежние маркеры → PASS |
| `tools/bench_test2_preflight.md` | строка контракта `p?`/`pdump` |
| `docs/AGENTS_STATUS.md` | строка занятости |
| `TZ_BENCH_TEST2_PREFLIGHT_PWM_MOE.md` | этот документ |

## 5. Проверки

- `python -m pytest tests/test_bench_test2_preflight.py tests/test_bench_test2_capture.py tests/test_bench_test2_simulation.py -q` — ALL PASS;
- `python -m py_compile tools/bench_test2_preflight.py`;
- `make` — production build PASS (C не меняется);
- `git diff origin/main...HEAD --check` — чисто;
- safety-модули (`src/`, `.ioc`) — 0 строк diff.

## 6. Запреты

- Не менять прошивку и `.ioc`;
- Не ослаблять fail-closed (отсутствие BDTR/маркеров = FAIL);
- Не «попутный рефакторинг».
