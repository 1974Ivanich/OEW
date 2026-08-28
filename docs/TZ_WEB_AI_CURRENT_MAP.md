# ТЗ для WEB AI: методика offline-снятия и оценки board-qualified current map

> Цель-потребитель: дать стещeму перечень задач и готовый материал для
> инженера-PК-3, который снимет карту тока на стенде и закомпилит её в
> board-specific профиль. Никакой лишней бюрократии: без отдельного
> safety-approval (уже есть Stage A 60V PASS), без протокольных форм.
> Только реальная работа.

---

## 1. Контекст (что уже известно, не перепроверять)

Проект: OEW Motor FOC, STM32G474RE, 2× STEVAL-IPM20B, асинхронный двигатель.
CMSIS-only, без HAL. Образ — commissioning (производственный default-deny, макросы
`OEW_MAP_CAPTURE=1`, `OEW_MAP_L3=1`, `PWM_OEW_BOARD_REVISION=7`).

Текущий статус стенда (ПК-3): Test №2 (ADC baseline), Test №3 (no-HV MapCapture),
Stage A 60V (energize-only) — все PASS. Control (FOC/V-f) запрещён и заблокирован,
пока нет board-qualified current map (сейчас `rc=-2 map_unverified`).

Готовые host-инструменты в main:
- `tools/map_artifact_pipeline.{c,h}` — pipeline L3: Accumulator→Solver→Certifier→Writer (host);
- `tools/map_characterization_adapter.{c,h}` — стенд: capture+scope+timing → MapMeasurementSample;
- `tools/map_artifact_writer.{c,h}` — запись карты/профиля;
- `tools/bench_test2_capture.py` — UART capture на стенде;
- `tools/campaign_demo/` — manifest.json + samples.jsonl (канонический формат);
- `tools/map_artifact_pipeline.md`, `tools/map_characterization_e2e_test.mk`.

Ключевое ограничение (MAP_L3_PIPELINE.md): снятие карты **offline**, полу-ручное:
- на стенде измеряются регионы (6 секторов × 2 окна = 12 строк) — ток через обмотки,
  осциллограф/скрипт;
- далее **оффлайн** оценивается матрица M (реконструкция тока) по измеренным точкам: агрегация
  по строкам, разброс, независимость уравнений, границы modulation, min_margin_ticks;
- результат компилируется в board-specific `MapBuilderQualification` (неизменяемый, compiled);
- `CurrentMap_LoadMeasured()` проверяет CRC, identity, reconstruction rows, регионы,
  стартовый контекст, overlap/coverage → только тогда `MAP_READY`.

## 2. Задача WEB AI

Подготовь **конкретную, исполнимую методику и артефакты** для снятия и оценки
board-qualified current map на стенде ПК-3. НЕ пиши общих слов, НЕ добавляй
бюрократию/approval/протоколы.

### 2.1. План эксперимента на стенде (что измерить и в какой последовательности)
- какие токи подавать (амплитуды/уровни), в каких секторах и окнах;
- сколько точек на строку, сколько записей минимально (`min_records_per_row`);
- как зафиксировать modulation vector и корректный регион (PWM region bounds);
- как снять `scope-reviewed` подтверждение, что в окне `ADC_FRAME_WINDOW_INVALID`.

### 2.2. Методика оценки матрицы M (offline)
- как агрегировать записи по строкам (усреднение, медиана, отсев выбросов);
- как оценить разброс и независимость уравнений (condition number матрицы M);
- как определить границы modulation и `min_margin_ticks` из измерений;
- как проверить, что 12 строк дают full-rank систему реконструкции.

### 2.3. Формат выходных данных → профиль
- точный формат строк `OewPwmRegion` и `CurrentReconEntry` (см. MAP_ACCUMULATOR_SPEC.md,
  MAP_L3_PIPELINE.md, tools/map_artifact_pipeline.h);
- как вычислить `startup_hold_cycles` и стартовую точку;
- чек-лист, что должен содержать скомпилированный профиль, чтобы
  `CurrentMap_LoadMeasured()` вернул MAP_READY (CRC, identity, full 12 rows, coverage).

### 2.4. Готовый скрипт/матрица
- дай минимальный Python-скрипт, который берёт `samples.jsonl`-подобные записи
  (idc1_ma, idc2_ma, vbus_mv, sector, window) и выдаёт: сводку по строкам, оценку M,
  condition number, границы modulation — прямо для компиляции в профиль.

## 3. Входные материалы (прочитать, они в main)
- `docs/MAP_L3_PIPELINE.md` — контракт профиля, ограничения builder/loader;
- `docs/MAP_ACCUMULATOR_SPEC.md` — спецификация аккумулятора (точность/формат);
- `tools/map_artifact_pipeline.h` — struct'ы MapMeasurementSample, CurrentReconEntry и т.п.;
- `tools/campaign_demo/samples.jsonl` + `manifest.json` — формат записей;
- `tools/map_characterization_e2e_test.mk` — как адаптер тестируется.

## 4. Формат ответа (что вернуть)
1. План эксперимента (раздел 2.1) — короткий, боевой, на одну сессию.
2. Методика оценки M (2.2) — формулы/шаги, condition number, критерии.
3. Схема заполнения профиля (2.3) — поля и откуда берутся значения.
4. Python-скрипт (2.4) — исполнимый, с обработкой samples.jsonl.
5. Список недостающих технических фактов, которые надо уточнить у инженера/по коду,
   прежде чем компилировать профиль (если такие есть).

Всё на русском. Без воды, без введения/благодарностей.
