# OEW Motor — FOC-привод на STM32G474RE

Проект OEW: полевой FOC-контроллер асинхронного двигателя на STM32G474RE
(Nucleo-G474RE) + 2× STEVAL-IPM20B (двойной инвертор, общий DC-link).
CMSIS-only (без HAL), C99. Прошивка + Python-GUI (`nucleo_debug_tool.py`).

## Совместная работа — обязательно для всех участников

Над проектом работают **люди и 4 ИИ-агента на 3 ПК**, в разное время.
Полный регламент: **[docs/AGENTS_WORKFLOW.md](docs/AGENTS_WORKFLOW.md)**.
Таблица занятости (кто какую ветку/файлы держит): **[docs/AGENTS_STATUS.md](docs/AGENTS_STATUS.md)**.

Ключевые правила (кратко):

1. **Push в `main` напрямую запрещён** — изменения попадают в main только
   через приёмку (один назначенный приёмщик). Локальный hook `main-guard`
   блокирует прямой push чужой веткой в main; CI проверяет каждую ветку.
2. **Каждая задача — отдельная ветка** `ai<N>/<задача>` от **свежего**
   `origin/main`:
   ```bash
   git fetch origin
   git checkout -b ai<N>/<задача> origin/main
   ```
3. **Перед публикацией** — `git fetch && git rebase origin/main`, затем
   push и подтверждение SHA:
   ```bash
   git ls-remote origin refs/heads/ai<N>/<задача>
   ```
4. **CI обязателен**: GitHub Actions (`build-test`) прогоняет на каждый push
   production build + hosted+QEMU тесты + commissioning build. Красный CI =
   пакет не принимается (ложные локальные «PASS» не считаются).
5. **Один пакет = одно ТЗ** (`TZ_*.md`). Попутный рефакторинг вне ТЗ
   отклоняется при приёмке.
6. **Зоны ответственности**: safety-модули (`src/foc*`, `src/pwm*`,
   `src/protect*`, `src/adc*`, `src/adc_dispatch.*`, `.ioc`) — только по
   явному ТЗ, один агент за раз.
7. Перед началом работы — запись в `docs/AGENTS_STATUS.md`
   (ветка / задача / файлы / статус).

## Текущий стендовый шаг — ПК‑3, test № 2 MapCapture no-HV

> **DC-link физически отключён; PC4/VBUS остаётся на нуле.** Цель — подтвердить ограниченный `MapCapture` path и его безопасное terminal shutdown, а не получить карту или разрешить FOC. PASS: `mcarm`/`run` возвращают `rc=0`, затем `term=-11` или `-12`, `records=0` и PWM выключен. Это **не** является автоматическим допуском к 60 В.

1. Оператор начинает с [пошаговой инструкции для ПК‑3](docs/BENCH_PC3_TEST2_NOHV.md).
2. Во время работы он заполняет [шаблон протокола test № 2](docs/templates/TEST2_NOHV_PROTOCOL_PC3.md) и сохраняет UART/build/trace evidence локально.
3. Переход к Stage A (DC-link 60 В) возможен только по явно оформленному GO/NO-GO gate в инструкции; synthetic `SYNT` на 60 В запрещён.

## Сборка и проверки

```bash
make                    # production (arm-none-eabi-gcc, Windows-тулчейн)
make test               # hosted + QEMU + pytest — ALL PASS
make EXTRA_CFLAGS="-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1 -DPWM_OEW_BOARD_REVISION=7"  # commissioning
python scripts/cubemx_check.py   # самоконтроль периферии (CubeMX)
```

Pre-push hook (обязателен на каждом ПК):
```bash
cp scripts/hooks/pre-push .git/hooks/
```

## Документация

- [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) — платформа, токовые каналы, состояние
- [ROADMAP.md](ROADMAP.md) — планы
- [OEW_SD_CHECKLIST.md](OEW_SD_CHECKLIST.md) — приёмка защиты (T1–T4, no-HV)
- [docs/BENCH_FIRST_SESSION.md](docs/BENCH_FIRST_SESSION.md) — чек-лист первой стендовой сессии
- [docs/BENCH_PC3_TEST2_NOHV.md](docs/BENCH_PC3_TEST2_NOHV.md) — **текущая пошаговая инструкция ПК‑3: test № 2 MapCapture no-HV**
- [docs/templates/TEST2_NOHV_PROTOCOL_PC3.md](docs/templates/TEST2_NOHV_PROTOCOL_PC3.md) — шаблон протокола test № 2 для фиксации результатов
- [docs/MAP_ACCUMULATOR_SPEC.md](docs/MAP_ACCUMULATOR_SPEC.md), [docs/MAP_L3_PIPELINE.md](docs/MAP_L3_PIPELINE.md) — L3 map pipeline
- [pinout.md](pinout.md) — распиновка
- `TZ_*.md` — ТЗ пакетов для внешних ИИ
