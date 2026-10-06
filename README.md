# OEW Motor — FOC-привод на STM32G474RE

Проект OEW: полевой FOC-контроллер асинхронного двигателя на STM32G474RE
(Nucleo-G474RE) + 2× STEVAL-IPM20B (двойной инвертор, общий DC-link).
CMSIS-only (без HAL), C99. Прошивка + Python-GUI (`nucleo_debug_tool.py`).

## Совместная работа

Над проектом работают люди и несколько ИИ-агентов на разных ПК, в разное время.
Правила — **[AGENTS.md](AGENTS.md)**. Доска занятости (кто держит какие файлы):
**[docs/AGENTS_STATUS.md](docs/AGENTS_STATUS.md)**.

Коротко: правь → `make` (если код) → push в `main`. Ветка — только для крупной
задачи. CI (`build-test`) — единственный гейт. Safety-модули (`src/foc*`,
`src/pwm*`, `src/protect*`, `src/vf*`, `src/adc*`, `.ioc`) — только по явному ТЗ.

## Сборка и проверки

```bash
make                    # production (arm-none-eabi-gcc, Windows-тулчейн)
make test               # hosted + QEMU + pytest — ALL PASS
make EXTRA_CFLAGS="-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1 -DPWM_OEW_BOARD_REVISION=7 -DOEW_MAP_SYNTHETIC_PROFILE=1 -DOEW_HOST_TEST=1 -DOEW_HS1_COMMISSIONING_RELEASE=1"  # commissioning
python scripts/cubemx_check.py   # сверка периферии с OEW_Motor.ioc (только если правил .ioc/пины/TIM/ADC/PLL)
```

Pre-push hook (опционально, только там, где установлен CubeMX):
```bash
cp scripts/hooks/pre-push .git/hooks/
git config hooks.cubemx-check false   # если проверка не нужна
```

## Документация

- [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) — платформа, токовые каналы, состояние
- [ROADMAP.md](ROADMAP.md) — планы
- [OEW_SD_CHECKLIST.md](OEW_SD_CHECKLIST.md) — приёмка защиты (T1–T4, no-HV)
- [docs/BENCH_FIRST_SESSION.md](docs/BENCH_FIRST_SESSION.md) — чек-лист первой стендовой сессии
- [docs/TEST2_TEST3_TRANSITION.md](docs/TEST2_TEST3_TRANSITION.md) — утверждённая граница: Test № 2 = ADC/measurement-chain baseline; будущий no-HV MapCapture = Test № 3, до отдельной G0 migration
- [docs/TEST2_ADC_CHAIN_PC3_PLAN.md](docs/TEST2_ADC_CHAIN_PC3_PLAN.md) — полный ПК‑3 план no-HV Test № 2.
- [docs/TEST2_QUICK_START.md](docs/TEST2_QUICK_START.md) — краткая техническая памятка DC-link/UART перед личным запуском Test № 2.
- [tools/bench_test2_capture.md](tools/bench_test2_capture.md) — legacy automation UART/sigrok для будущей Test № 3; machine-readable migration с `HIL_TEST2_G0`/`MAPCAP_TEST2` пока не принята

- [tools/bench_test2_simulation.md](tools/bench_test2_simulation.md) — ПК‑1: software-HIL execution pipeline test № 2 без физического оборудования; результат не является стендовым PASS
- [tools/bench_test2_g0_check.md](tools/bench_test2_g0_check.md) — offline fail-closed проверка approval, manifest, build log и firmware identity до physical no-HV Test № 2
- [tools/bench_test2_campaign_archive.md](tools/bench_test2_campaign_archive.md) — ПК‑3: неизменяемый ZIP и SHA-256 inventory evidence после завершения physical Test № 2
- [tools/bench_test2_preflight.md](tools/bench_test2_preflight.md) — ПК‑3: fail-closed CLI pre-flight G0, no-HV confirmations, UART observations и sigrok discovery без `mcarm/run`

- [docs/MAP_ACCUMULATOR_SPEC.md](docs/MAP_ACCUMULATOR_SPEC.md), [docs/MAP_L3_PIPELINE.md](docs/MAP_L3_PIPELINE.md) — L3 map pipeline
- [pinout.md](pinout.md) — распиновка
- `TZ_*.md` — ТЗ пакетов для внешних ИИ
