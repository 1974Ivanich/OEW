# Порядок получения region_*.log + scope_region_*.csv для grid-кампании BOAR

Этот документ описывает, как на ПК‑3 снять UART‑логи `region_0.log` …
`region_11.log` и осциллографические CSV `scope_region_0.csv` …
`scope_region_11.csv`, которые затем скармливаются `tools/map_scope_ingest.py` на
ПК‑2 для построения кампании BOAR.

> **ВАЖНО:** это уже не de‑energized Phase 1. Для `mapcap build/run` на стенде
> используется commissioning‑образ, который может выдавать управляющие PWM‑импульсы
> (хотя DC‑link ещё не обязан быть на полном 60 В, но силовая схема подключается).
> Поэтому перед началом требуется:
> - approved Test №3 G0 (см. `TZ_MAP_CAPTURE_BOARD_PROFILE.md`);
> - written procedure, двухсторонний safety checklist, LOTO;
> - утверждённый board‑qualified профиль в `src/map_capture_profiles.c`;
> - топология стенда согласована с профилем. На стенде ПК‑3 мотор включён
>   **open‑winding**: каждая фаза между `TIM1_x` и `TIM8_x`. Профиль BOAR v2
>   задаёт для TIM8 **встречный** вектор (циклический сдвиг TIM1); одинаковые
>   CCR на TIM1/TIM8 дают нулевой ток через обмотки и zero‑current evidence.
> Без этих артефактов capture‑команды заблокированы — и это правильно.
>
> **Safety checklist:** см. `SAFETY_CHECKLIST_PC3.md` в этой папке.

## 0. Что должно быть готово до похода на стенд

1. **В `main` влит board‑qualified BOAR‑профиль** (`ai2/map-board-profile` или
   аналогичная ветка, CI зелёный, приёмка).
   - Профиль id, например `0x424F4152` (`BOAR`) либо 12 вариантов
     `base + sector*2 + window`.
   - Профиль содержит 12 `MapCaptureRequest` (6 секторов × 2 окна),
     16 `pulse_count`, modulation‑вектора по контракту VfcApertureContract.
   - `MapBuilderQualification` со `scope_qualified`‑проверками,
     `recon.valid=false` (коэффициенты M оцениваются офлайн).
   - TIM8‑вектор — циклический сдвиг TIM1: open‑winding требует
     разности потенциалов на концах обмотки; равные CCR → нулевой ток.
2. **Calibration JSON** от Phase 1: `acs712_calibration.json`
   (`vcc_mv`, `v0_U`, `v0_V`, `sens_mv_per_a`) — понадобится на ПК‑2 при ingest.
3. **Оборудование:**
   - стенд STEVAL‑IPM20B ×2 + STM32G474RE;
   - DC‑link 60 В с токоограничением и emergency stop;
   - 2× ACS712‑20A (CH1 = фаза U, CH2 = фаза V), питание 5.0 В,
     `CF ≤ 1 нФ`;
   - осциллограф (external trigger PB6: HIGH на время MapCapture service burst,
     LOW после PWM disable) + UART‑кабель;
   - safety watcher.

## 1. Сборка и прошивка commissioning‑образа на ПК‑3

```powershell
make clean
make EXTRA_CFLAGS="-DOEW_MAP_CAPTURE=1 -DOEW_MAP_L3=1 -DPWM_OEW_BOARD_REVISION=7 -DOEW_HS1_COMMISSIONING_RELEASE=1"
make flash
```

После прошивки проверить identity:

```text
sysinfo
p?
pdump
```

Ожидается: PWM off, no active output, `mapcap`‑команды доступны.

## 2. Readiness check перед energize

### 2.1. G0 approval JSON

Шаблон: `g0_approval_template.json` в этой папке. Заполнить и получить
подпись approver:

- `decision` → `APPROVED`;
- `firmware_source_sha` → SHA текущего `origin/main`;
- `firmware_sha256` → SHA256 `build/firmware.bin` commissioning-сборки;
- подписи operator / safety watcher.

### 2.2. Интерактивный скрипт-чеклист

Запустить на ПК‑3 перед подачей DC-link:

```powershell
python tools\boar_energize_ready.py `
  --g0-approval "D:\campaign_raw\boar_...\g0_approval.json" `
  --operator "Иван Иванов" `
  --watcher "Пётр Петров" `
  --output "D:\campaign_raw\boar_...\energize_ready.json"
```

Скрипт проверит:
- G0 approval APPROVED для TEST3 и scope содержит mapcap;
- git‑дерево чистое и на `main`;
- commissioning‑сборка (или задан `--firmware-bin`) и SHA256 совпадает с G0;
- пошагово спросит/запишет safety items (LOTO, E-stop, current limit, wiring,
  двусторонний контроль и т.д.).

Exit 0 и файл `energize_ready.json` → можно переходить к LOTO/energize.

### 2.3. LOTO и energize

Только после успешного `boar_energize_ready.py`:
- применить LOTO к DC-link;
- подключить моторные провода;
- снять LOTO по двойному подтверждению;
- плавно подать 60 В.

## 3. Базовая MapCapture‑последовательность (grid v2: 4 точки на регион)

Профиль BOAR v2 задаёт **48 вариантов** (`6 секторов × 2 окна × 4 точки`).
Для каждого региона `r = 0..11`:
- `sector = r // 2`
- `window = r % 2`
- точки `point = 0..3`

ID варианта:

```text
profile_id = 0x424F4152 + r*4 + point   (= 1112490322 + r*4 + point)
```

Пример для `r = 0`, `point = 0`:

```text
mapcap build=1112490322   # base + 0
mcarm=1112490322          # arm вариант (sector=0, window=0, point=0)
mapcap run                # запуск capture burst (8 импульсов)
mapcap drain              # выводит 8 строк @MC:REC + @MC:DRAIN:records=8
mapcap status             # COMPLETE, detail=0, fault=0
```

Сохранить весь UART‑лог сессии как `region_<r>_<point>.log`:

```powershell
# В PuTTY/teraterm/minicom включите логирование в файл.
# После каждой сессии переименуйте файл:
Move-Item uart_session.log "region_${r}_${point}.log"
```

Требования к логу:
- ровно **8 строк `@MC:REC:`**;
- одна строка `@MC:DRAIN:records=8`;
- `mapcap status` → COMPLETE, detail=0;
- CCR в REC совпадают с ожидаемым modulation‑вектором варианта
  (иначе `map_scope_ingest.py` fail‑closed).

Таким образом, для каждого региона будет **4 файла логов**:

```text
region_0_0.log .. region_0_3.log
region_1_0.log .. region_1_3.log
...
region_11_0.log .. region_11_3.log
```

(Итого 48 логов и 48 CSV.)

## 4. Scope CSV для точки

Осциллограф должен измерить напряжение на выходах ACS712 (фаза U и V) в
моменты ADC sample (точки, заданные профилем). Для каждого из **8 импульсов**
записать:

- `ref_u_mv` — CH1, мВ;
- `ref_v_mv` — CH2, мВ;
- `ref_w_mv` — оставить пустым (KCL: `ref_w = -(ref_u + ref_v)`);
- `margin_ticks` — aperture margin, обычно 110;
- `blanking_ticks` — sample window, обычно 15;
- `scope_qualified` — `1` только после ручной/автоматической проверки aperture;
- `note` — например `ACS712 CH1=U CH2=V sector=0 window=0`.

CSV‑шаблон см. `docs/templates/test3_nohv_campaign/scope/scope_region_template_acs712.csv`.

Имя файла: `scope_region_<r>_<point>.csv`.

Пример набора для `r=0`:

```text
scope_region_0_0.csv
scope_region_0_1.csv
scope_region_0_2.csv
scope_region_0_3.csv
```

## 5. Проверка на ПК‑3 между сессиями

После каждой сессии:

```text
p?
pdump
```

PWM должен быть off, fault=0. Если появился fault или сработал interlock —
остановиться, задокументировать, сбросить по процедуре.

## 6. Структура кампании после 12 сессий

```text
D:\campaign_raw\boar_2026<MM><DD>T<HHMMSS>Z\
  logs\
    region_0_0.log .. region_0_3.log
    ...
    region_11_0.log .. region_11_3.log
  scope\
    scope_region_0_0.csv .. scope_region_0_3.csv
    ...
    scope_region_11_0.csv .. scope_region_11_3.csv
  calibration\
    acs712_calibration.json
```

## 7. Шаблон и pre-check (опционально)

На ПК‑3 можно создать пустой каркас кампании с заглушками и сразу
скопировать `acs712_calibration.json` из принятого Phase‑1 пакета:

```powershell
python tools\boar_campaign_template.py `
  --campaign-root "D:\campaign_raw\boar_20260905T120000Z" `
  --calibration "C:\campaign_raw\accepted\acs712_nohv_20260904T040619Z\calibration\acs712_calibration.json"
```

Это создаёт все 48 `region_<r>_<p>.log` и 48 `scope_region_<r>_<p>.csv`.
Заглушки явно помечены `placeholder` и `scope_qualified=0`; их надо заменить
реальными логами/измерениями.

Перед передачей на ПК‑2 проверить комплектность:

```powershell
python tools\verify_boar_campaign_ready.py `
  --campaign-root "D:\campaign_raw\boar_20260905T120000Z"
```

Exit 0 = все файлы на месте и заглушки убраны, можно передавать на ingest.

## 8. Передача и ingest на ПК‑2

Скопировать папку на ПК‑2, затем можно запустить полный pipeline одной командой:

```powershell
python tools\boar_campaign_ingest.py `
  --campaign-root "D:\campaign_raw\boar_2026..." `
  --pipeline
```

Скрипт автоматически:
1. Выполняет `verify_boar_campaign_ready.py`.
2. Запускает `map_scope_ingest.py --logs ... --scope ... --out ... --calib ...`.
3. При `--pipeline` запускает host pipeline CLI и кладёт артефакт в
   `campaign/pipeline/oew_map_v2.bin`.

Или запускать вручную:

```powershell
python tools\map_scope_ingest.py `
  --logs "D:\campaign_raw\boar_2026...\logs" `
  --scope "D:\campaign_raw\boar_2026...\scope" `
  --out "D:\campaign_raw\boar_2026...\campaign" `
  --calib "D:\campaign_raw\boar_2026...\calibration\acs712_calibration.json"
```

Если всё в порядке, создаётся `campaign/manifest.json` + `campaign/samples.jsonl`,
которые принимает `map_bench_dataset.py`. Exit code 0 = кампания готова.

## 9. Архивирование готовой кампании

После успешного ingest можно создать deterministic ZIP для хранения/передачи:

```powershell
python tools\boar_campaign_archive.py `
  --campaign-root "D:\campaign_raw\boar_2026..." `
  --out "D:\campaign_raw\boar_2026....zip"
```

Создаётся:
- `boar_2026....zip` — детерминированный ZIP (сортировка, фиксированные
  timestamp 1980-01-01, ZIP_DEFLATED);
- `boar_2026....receipt.json` — инвентарь файлов + SHA-256 архива.

## 11. Статус board‑профиля

Board‑qualified профиль BOAR v2 **уже реализован** в `src/map_capture_profiles.c`
(ветка `main`, см. коммиты `00be21e`, `f3f8330`, `6394219`).
CI‑тест `tests/map_capture_board_profile_test.c` проходит в workflow `build-test`.

Полная 60 В grid-кампания сейчас **BLOCKED** после двух диагностических
попыток 2026-09-04: первая выявила нулевой дифференциал TIM1/TIM8, вторая после
исправления профиля защёлкнула `FAULT_R=18`. Третью попытку при 60 В не проводить.

Low-voltage bring-up 10 В также завершён BLOCKED: `FAULT_R=18` возник при
подаче DC-link до любой MapCapture-команды, с PWM off. Burst не выполнялся.
First-break диагностика на следующем Step E классифицировала вход: `src=TIM8`,
TIM8 SR=0x81 (BIF+UIF), TIM1 SR=0x01, `CCER=0`, capture idle. Burst-overcurrent
для события исключён; break пришёл через TIM8 BKIN/PD2/SD2.

Физический источник и длительность импульса пока не доказаны. После четырёх
последовательных чистых energize-only 10 В и validated armed SD2 trigger
(NO_EVENT за 150 с, `FAULT=0`, `breakdiag valid=0`) Step E принят.

**Step A 10 V — PASS (2026-09-05).** One `r0p0` burst completed: 8/8 records,
`term=0`, `FAULT=0`, SD1/SD2 high, PB6 marker observed on LA, shunt ADC
`i1` up to 294 mA / `i2` up to 255 mA (nonzero, non-saturated). PWM off after
capture. ACS712 scope evidence was not required at 10 V (SNR < 1 at < 0.5 A;
shunt ADC is authoritative). See `docs/STEP_A_ACCEPTANCE.md` for formal
acceptance and evidence hashes.

Следующий этап — Step B (15 V) или возврат к 60 В grid-кампании, каждый
требует отдельного G0 и нового evidence package. BKIN остаётся включённым.

## 12. Запреты

- Не запускать `mapcap run` без safety watcher и LOTO.
- Не подавать DC‑link без токоограничения.
- Не оставлять commissioning‑образ на плате после сессии — вернуть
  production default‑deny (`make clean && make && make flash`).
- Не вписывать `scope_qualified=1` без реальной проверки aperture.
