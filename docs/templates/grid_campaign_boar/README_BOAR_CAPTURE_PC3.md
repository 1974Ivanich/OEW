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
> - утверждённый board‑qualified профиль в `src/map_capture_profiles.c`.
> Без этих артефактов capture‑команды заблокированы — и это правильно.

## 0. Что должно быть готово до похода на стенд

1. **В `main` влит board‑qualified BOAR‑профиль** (`ai2/map-board-profile` или
   аналогичная ветка, CI зелёный, приёмка).
   - Профиль id, например `0x424F4152` (`BOAR`) либо 12 вариантов
     `base + sector*2 + window`.
   - Профиль содержит 12 `MapCaptureRequest` (6 секторов × 2 окна),
     16 `pulse_count`, modulation‑вектора по контракту VfcApertureContract.
   - `MapBuilderQualification` со `scope_qualified`‑проверками,
     `recon.valid=false` (коэффициенты M оцениваются офлайн).
2. **Calibration JSON** от Phase 1: `acs712_calibration.json`
   (`vcc_mv`, `v0_U`, `v0_V`, `sens_mv_per_a`) — понадобится на ПК‑2 при ingest.
3. **Оборудование:**
   - стенд STEVAL‑IPM20B ×2 + STM32G474RE;
   - DC‑link 60 В с токоограничением и emergency stop;
   - 2× ACS712‑20A (CH1 = фаза U, CH2 = фаза V), питание 5.0 В,
     `CF ≤ 1 нФ`;
   - осциллограф (синхронизация по PWM/триггеру) + UART‑кабель;
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

## 2. Базовая MapCapture‑последовательность (одна сессия = один регион)

Для каждого региона `r = 0..11`:
- `sector = r // 2`
- `window = r % 2`

Если профиль реализован как 12 вариантов с id `base + r`, используйте команду
`mapcap build=<profile_id>`; если профиль один с параметризацией — смотрите
утверждённую процедуру из ТЗ.

Пример для профиля‑варианта:

```text
mapcap build=1112490514   # base + r
mcarm=1112490514          # arm выбранный вариант
mapcap run                # запуск capture burst (16 импульсов)
mapcap drain              # выводит 16 строк @MC:REC + @MC:DRAIN:records=16
mapcap status             # COMPLETE, detail=0, fault=0
```

Сохранить весь UART‑лог сессии как `region_<r>.log`:

```powershell
# В PuTTY/teraterm/minicom включите логирование в файл.
# После каждой сессии переименуйте файл:
Move-Item uart_session.log "region_${r}.log"
```

Требования к логу:
- ровно 16 строк `@MC:REC:`;
- одна строка `@MC:DRAIN:records=16`;
- `mapcap status` → COMPLETE, detail=0;
- CCR в REC совпадают с ожидаемым modulation‑вектором региона
  (иначе `map_scope_ingest.py` fail‑closed).

## 3. Scope CSV для региона

Осциллограф должен измерить напряжение на выходах ACS712 (фаза U и V) в
моменты ADC sample (точки, заданные профилем). Для каждого из 16 импульсов
записать:

- `ref_u_mv` — CH1, мВ;
- `ref_v_mv` — CH2, мВ;
- `ref_w_mv` — оставить пустым (KCL: `ref_w = -(ref_u + ref_v)`);
- `margin_ticks` — aperture margin, обычно 110;
- `blanking_ticks` — sample window, обычно 15;
- `scope_qualified` — `1` только после ручной/автоматической проверки aperture;
- `note` — например `ACS712 CH1=U CH2=V sector=0 window=0`.

CSV‑шаблон см. `docs/templates/test3_nohv_campaign/scope/scope_region_template_acs712.csv`.

Имя файла: `scope_region_<r>.csv`.

## 4. Проверка на ПК‑3 между сессиями

После каждой сессии:

```text
p?
pdump
```

PWM должен быть off, fault=0. Если появился fault или сработал interlock —
остановиться, задокументировать, сбросить по процедуре.

## 5. Структура кампании после 12 сессий

```text
D:\campaign_raw\boar_2026<MM><DD>T<HHMMSS>Z\
  logs\
    region_0.log .. region_11.log
  scope\
    scope_region_0.csv .. scope_region_11.csv
  calibration\
    acs712_calibration.json
```

## 6. Передача и ingest на ПК‑2

Скопировать папку на ПК‑2, затем:

```powershell
python tools\map_scope_ingest.py `
  --logs "D:\campaign_raw\boar_2026...\logs" `
  --scope "D:\campaign_raw\boar_2026...\scope" `
  --out "D:\campaign_raw\boar_2026...\campaign" `
  --calib "D:\campaign_raw\boar_2026...\calibration\acs712_calibration.json"
```

Если всё в порядке, создаётся `campaign/manifest.json` + `campaign/samples.jsonl`,
которые принимает `map_bench_dataset.py`. Exit code 0 = кампания готова.

## 7. Что делать, если сейчас нет approved board‑профиля

См. `TZ_MAP_CAPTURE_BOARD_PROFILE.md` в корне репозитория. Это отдельное ТЗ:

1. Добавить профиль в `src/map_capture_profiles.c` (+ тесты).
2. Ветка `ai2/map-board-profile`, CI green, code review, приёмка.
3. Только после вливания в `main` идти на стенд.

## 8. Запреты

- Не запускать `mapcap run` без safety watcher и LOTO.
- Не подавать DC‑link без токоограничения.
- Не оставлять commissioning‑образ на плате после сессии — вернуть
  production default‑deny (`make clean && make && make flash`).
- Не вписывать `scope_qualified=1` без реальной проверки aperture.
