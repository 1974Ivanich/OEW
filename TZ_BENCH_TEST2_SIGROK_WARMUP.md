# ТЗ: sigrok — дефолтный warmup 0.05 с для попадания пачки в окно fx2lafw

Статус: черновик пакета `ai4/bench-test2-sigrok-warmup`

## 1. Проблема

Физический Test №2 (run2/4/5/6/7, 25.08.2026) показал: логический анализатор
`fx2lafw` (Cypress FX2) **не тянет захват дольше ~160 мс** — sigrok-cli сообщает
`Device only sent 1286144 samples` (лимит по времени, не зависит от частоты:
8 МГц → 1.28M сэмплов = 160 мс; 1 МГц → 169K = 169 мс).

Дефолтный `--capture-warmup-seconds 0.30` сдвигает `mapcap run` (и PWM-пачку)
на +300 мс от старта захвата — **за пределы 160-мс окна**. В результате
`sigrok_digital.csv` пустой (run2/4/5), хотя PWM физически коммутирует.

Рабочий параметр `--capture-warmup-seconds 0.05`: run срабатывает на +50 мс,
пачка (~250 мкс) попадает в первые 160 мс — CSV содержит burst (run6/7).

## 2. Цель

Сделать дефолтный warmup **0.05 с** (вместо 0.30), чтобы пачка по умолчанию
попадала в окно захвата. Задокументировать лимит fx2lafw ~160 мс.

## 3. Обоснование значения

- 0.30 с → пачка за окном (потеря evidence) — плохо.
- 0.05 с → пачка на ~50..55 мс от старта — в окне (подтверждено run6/7).
- Меньше (0.0) — риск, что sigrok-cli ещё не начал приём (инициализация USB);
  0.05 эмпирически достаточно.

## 4. Объём изменений

| Файл | Изменение |
|---|---|
| `tools/bench_test2_capture.py` | дефолт `--capture-warmup-seconds` 0.30 → **0.05** + комментарий о лимите fx2lafw |
| `tools/bench_test2_capture.md` | раздел про реальный запуск: лимит ~160 мс, warmup 0.05, ожидаемый CSV |
| `tests/test_bench_test2_capture.py` | тест дефолта warmup == 0.05 |
| `docs/AGENTS_STATUS.md` | строка занятости |
| `TZ_BENCH_TEST2_SIGROK_WARMUP.md` | этот документ |

## 5. Проверки

- `python -m pytest tests/test_bench_test2_capture.py tests/test_bench_test2_simulation.py tests/test_bench_test2_preflight.py tests/test_bench_test2_g0_check.py -q` — ALL PASS;
- `python -m py_compile tools/bench_test2_capture.py`;
- `make` — production build PASS (C не меняется);
- `git diff origin/main...HEAD --check` — чисто;
- safety-модули (`src/`, `.ioc`) — 0 строк diff.

## 6. Запреты

- Не менять прошивку и `.ioc`;
- Не менять `capture-seconds` (остаётся 1.5; устройство всё равно обрезает);
- Не «попутный рефакторинг».
