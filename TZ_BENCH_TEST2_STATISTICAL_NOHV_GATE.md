# ТЗ: статистический no-HV гейт raw_vbus для Test №2 (preflight + capture)

Статус: черновик пакета `ai4/bench-test2-statistical-nohv-gate`

## 1. Проблема

No-HV гейт `raw_vbus <= nohv_max_raw_vbus (9)` в `bench_test2_preflight.py` и
`bench_test2_capture.py` использует **одиночный** сэмпл канала PC4/ADC2_IN5.

На стенде (ПК-3) при физически **0 В** на шине (DMM: PC4 и J2.pin14 = 0 В стабильно)
канал даёт случайные шумовые выбросы:
- single-shot `a`: baseline 0..4, редкие выбросы 10..26 (до 142 в одной ранней серии);
- поток a=50 (суммарно ~900 сэмплов): ~2..3% сэмплов > 9, максимум 61 (эта сессия),
  единичный 142 (ранняя сессия);
- медиана по окнам N=20 всегда <= 4.

Одиночный порог `<= 9` на таком канале ложноположительно (false-fail) роняет
preflight/test ~2..3% попыток, хотя аппаратура исправна. Доказательства исправности:
- 3.3 В на PC4 -> raw 4095 стабильно (полная шкала);
- 60 В на STEVAL -> 0.48 В на PC4 -> raw ~595..631 стабильно (делитель 1:125 работает);
- DR register совпадает с raw_vbus (путь ADC->frame->telemetry консистентен).

## 2. Цель

Заменить одиночный порог статистическим критерием для наблюдения `a` (VBUS):
по N сэмплам, медиана <= 9 (основное доказательство no-HV) и max <= жёсткого предела
(страховка от грубых аномалий). Критерий обязан fail-closed для ЛЮБОГО реального
напряжения шины >= ~1 В (raw >= 10 => медиана >= 10 => FAIL).

## 3. Контракт (после изменения)

| Параметр | Значение | Обоснование |
|---|---|---|
| `VBUS_SAMPLES` (N) | 20 | достаточно для робастной медианы; ~4..5 с на стенде |
| `nohv_max_raw_vbus` (медиана) | 9 | без изменений: медиана <= 9 доказывает шину < ~0.9 В |
| `nohv_max_raw_vbus_hard` (max) | 200 | max наблюдаемого шума при 0 В = 142 (DMM-доказано 0 В) + запас; raw 200 ~ 20 В шины — грубая аномалия |
| Проверка I1/I2 | не насыщены (< 32767) для каждого сэмпла | без изменений |

Критерий PASS для наблюдения `a` (preflight и capture `adc_before`):
```
all(parsed) AND median(raw_vbus) <= 9 AND max(raw_vbus) <= 200
    AND all(|i1| < 32767) AND all(|i2| < 32767)
```
Примечание по безопасности: реальное напряжение >= ~1 В даст медиану >= 10 -> FAIL.
Жёсткий предел 200 не может «пропустить» реальное напряжение, т.к. медиана всё равно
его поймает; предел страхует только от грубых сбоев измерения.

## 4. Объём изменений

Только host-инструменты и их тесты/доки. **Прошивка (firmware) НЕ меняется.** Терминальный
`@MC:STATUS` raw_vbus (frozen frame от injected-пути) остаётся одиночным <= 9 — статистика
к замороженному кадру неприменима; это фиксируется в доке как известное ограничение.

| Файл | Изменение |
|---|---|
| `tools/bench_test2_capture.py` | `DEFAULT_VBUS_SAMPLES=20`, `NOHV_RAW_VBUS_HARD_LIMIT=200`; `--vbus-samples`; `adc_before` = N команд `a`; `evaluate_test` принимает список текстов, считает медиану/max; контракт + `nohv_max_raw_vbus_hard` |
| `tools/bench_test2_preflight.py` | `--vbus-samples`; наблюдение `a` = N сэмплов; статистическая проверка через `_CAPTURE`; транскрипт: `a` может быть строкой или списком; в summary — сэмплы/медиана/max |
| `tests/test_bench_test2_capture.py` | `evaluate_test` с списком; новые тесты статистического критерия (медиана<=9, max<=200, fail на реальном напряжении) |
| `tests/test_bench_test2_simulation.py` | `EXPECTED_GOOD_SEQUENCE` с `["a"]*N` |
| `tests/test_bench_test2_preflight.py` | фикстуры транскрипта (a как список/строка); тесты статистики |
| `tools/bench_test2_preflight.md` | строка контракта `a` (статистический критерий) |
| `tools/bench_test2_capture.md` | описание `adc_before` (N сэмплов) и ограничения терминального кадра |
| `docs/AGENTS_STATUS.md` | строка занятости |
| `TZ_BENCH_TEST2_STATISTICAL_NOHV_GATE.md` | этот документ |

## 5. Проверки

- `python -m pytest tests/test_bench_test2_capture.py tests/test_bench_test2_simulation.py tests/test_bench_test2_preflight.py -q` — ALL PASS;
- `python -m py_compile tools/bench_test2_capture.py tools/bench_test2_preflight.py`;
- `make` — production build PASS (C-код не меняется);
- `git diff origin/main...HEAD --check` — чисто;
- safety-модули (src/, .ioc) — 0 строк diff.

## 6. Запреты

- Не менять `nohv_max_raw_vbus` (9) и не ослаблять доказательство no-HV;
- Не менять прошивку, `.ioc`, распиновку;
- Не «попутный рефакторинг» вне пунктов ТЗ.
