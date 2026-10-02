# ТЗ: SimpleFOC v2.4.0 как внешний reference для четырёх узлов OEW current-sensing архитектуры (без переноса кода)

Ветка для исполнения: **новая** от актуального `main` репозитория OEW
(github.com/1974Ivanich/OEW). Формат исполнения — по конвенции репо:
исследование-сравнение + документ вердиктов, **firmware не тронут** (0 строк в
`src/`, `main.c`, `Makefile`, `.github/workflows/ci.yml`).

Заказчик (по заключению, 01.10.2026): SimpleFOC полезен **не как готовая FOC-библиотека**,
а как внешний reference по current-sensing architecture. Переносить SimpleFOC
целиком нельзя: SimpleFOC ориентирован на BLDC/Stepper с одним инвертором, а
наша задача — open-end winding асинхронная машина с двумя силовыми тройками
(`Vphase = V_inv1 − V_inv2`). Runtime, map/reconstruction архитектуру — не
переносить. Map-basis работа (`MAP_BASIS_FIX`) остаётся необходимой и не
заменяется SimpleFOC.

Приоритеты из заключения:

1. `driverAlign()` — очень полезно (у нас уже есть report-only реализация `al`,
   `docs/TZ_ALIGN_CHECK_AND_TWO_GATE_FIRST_START_PC3.md` — сверить рецепты, не
   поведение);
2. STM32 G4 ADC/PWM synchronization — очень полезно;
3. low-side sampling/aperture model — очень полезно;
4. Id/Iq diagnostic methodology — полезно (готовая классификация симптомов);
5. FOC runtime — практически не нужен;
6. готовая map/reconstruction архитектура — не переносить.

Отдельный сигнал: SimpleFOC v2.4.0 фиксит потерю синхронизации low-side sensing
на G431 — даже зрелая библиотека получает специальные фиксы именно в
ADC/PWM-синхронизации. Это аргумент за независимую сверку нашего тракта.

## 1. Scope: четыре узла сравнения

Каждый узел: изучить SimpleFOC-подход по источникам, сравнить с нашей
реализацией, выпустить вердикт `CONFIRMED | ADAPTED | NOT_APPLICABLE` с
обоснованием. Запрещено: переносить код, менять прошивку, "улучшать" наш
тракт по мотивам SimpleFOC без отдельного ТЗ.

### Узел 1 — STM32 G4 low-side current sensing

SimpleFOC: low-side sensing на STM32 G4 строится вокруг **injected conversions**;
ток физически измерим только в определённых состояниях PWM.

Наш тракт: TIM1 TRGO → dual injected (ADC1+ADC2), JEOS-пара, окна
`ci 0/1/2`, `src/adc.c`.

Вопросы сравнения:

- где в их G4-backend выбирается момент выборки относительно центра
  center-aligned PWM; что они считают безопасным окном (BC/BC′-геометрия);
- как обрабатывается случай «ток не измерим» (нет проводящего состояния) —
  есть ли эквивалент нашего `WINDOW_INVALID` и как он отражается в verdict;
- эквивалентны ли наши `PWM_OEW_ADC_TRIGGER_REVISION`-гарды их практике
  pin/revision-проверок (модель «устройство отвечает за свой секрет» vs
  fail-closed identity).

### Узел 2 — PWM/ADC synchronization

SimpleFOC: синхронизация выборки с PWM — критическая часть low-side sensing;
v2.4.0 содержит фикс потери синхронизации на G431.

Наш тракт: TRGO 5 кГц center-aligned → injected JEOS-пара 10 кГц; маркеры PA4
(TRGO/UIF) и PA5 (JEOS) под `OEW_BENCH_APERTURE=1` (`main.c:63-99`);
`sampling_qual.py` измеряет фазу JEOS-маркера и её джиттер, дрейф между
кадрами, и выдаёт `SAMPLING_QUALIFIED / SUSPECT` без подгонки.

Вопросы сравнения:

- какие механизмы детектирования рассинхрона есть у SimpleFOC и есть ли
  эквивалент нашего джиттер/дрейф-гейта;
- как они обрабатывают multiple-motor low-side case (v2.4.0 changelog);
- может ли `sampling_qual.py` (Level 2) считаться внешне-обоснованным
  эквивалентом их инвариантов, и чего не хватает (e.g. проверка при
  изменяющемся duty, а не только статический кадр).

### Узел 3 — driverAlign / current-sense alignment

SimpleFOC: прикладывает известное напряжение к фазам, сверяет направление
тока; ловит перепутанные pin и инвертированный gain; коды результата
(0=fail/1=ok/2=pins swapped/3=gains inverted/4=both).

Наша реализация (уже в ветке `ai2/shunt-cal-campaign-v1`, `4cd540f`): команда
`al`, PWM-апертура `PWM_AlignAperture*` (TIM1+TIM8, дифференциальный вектор
±6% при ARR=999, MOE только после SetVector), коды 0/1/2/3 в ответе
`@AL:OK:...align=...`; вердикт — оператор offline, прошивка коэффициенты не
принимает (политика репо).

Вопросы сравнения:

- их критерий «направление тока» против нашего «знак I1/I2 при известном
  дифференциальном векторе»: эквивалентны ли коды 0–4;
- **ключевое отличие OEW**: у нас открытая обмотка — одинаковые CCR TIM1/TIM8
  дают нулевой ток, поэтому вектор обязан быть дифференциальным
  (`Inv1−Inv2` basis). Их рецепт не знает про двухинверторность — зафиксировать
  это как причина, почему копирование невозможно;
- можно ли их идею «auto-detect и предложить fix» взять как **offline-инструмент**
  (не прошивка): `al`-снимок + авто-вердикт в `shunt_cal_campaign.py` /
  отдельном анализаторе (report-only, прошивка не меняется).

### Узел 4 — regular ADC reads при активном injected sensing

SimpleFOC: `_readRegularADCVoltage()` учитывает текущую конфигурацию ADC и
injected conversions, чтобы дополнительные измерения не ломали current-sampling
loop.

Наш открытый вопрос (стендовая диагностика): команда `a` → `adc2_read()` →
`JADSTP` → `JADSTART` → **потерян TRGO/JEOS** — регулярное чтение ломает
injected-цикл. Это наш точный аналог их проблемы.

Вопросы сравнения:

- как именно SimpleFOC решает конфликт regular vs injected на G4 (ожидание
  окна, отдельный ADC-мастер, отложенное чтение);
- применимо ли к нашей схеме: может ли `a`/`@ADC`-путь читать **загруженный
  JEOS-фрейм** (данные уже собраны триггером) вместо ручного старта —
  candidate-fix для отдельного ТЗ, не в этом исследовании;
- что SimpleFOC делает с VBUS (regular channel) во время FOC — сравнить с нашим
  `cv`/VBUS-трактом.

## 2. Формат результата

Документ `docs/NOTES_SIMPEFOC_G4_REFERENCE.md` (или аналогичный, по конвенции):
на каждый узел — сводка их подхода с цитатами/ссылками (docs.simplefoc.com,
GitHub master), наш подход (файл:строка), вердикт `CONFIRMED / ADAPTED /
NOT_APPLICABLE`, список candidate-fix'ов (каждый — кандидат в отдельное ТЗ,
без реализации).

Критерии приёмки:

1. каждый узел имеет вердикт и доказательную базу (ссылки на SimpleFOC-код
   версии v2.4.0, не «где-то читал»);
2. зафиксировано явно: перенос runtime/map-архитектуры отклонён; замороженный
   static-aperture experiment не переписывается под SimpleFOC;
3. candidate-fix'ы не реализуются в этом проходе (в т.ч. JEOS-фрейм fix для
   `a`);
4. firmware/CI/Makefile не тронуты; `make test-py` зелёный (если затронуты
   tools);
5. AGENTS_STATUS.md: строка о работе по конвенции.

## 3. Вне scope

- FOC runtime SimpleFOC (их PI/speed/torque-контур) — не изучается;
- их map/reconstruction архитектура — не переносится;
- Commander CLI — уже отклонён ранее (наш протокол строже);
- изменение замороженного static-aperture experiment;
- изменение нашей лестницы гейтов (align → dc_current → dq) — она уже
  согласована в `docs/TZ_ALIGN_CHECK_AND_TWO_GATE_FIRST_START_PC3.md`.
