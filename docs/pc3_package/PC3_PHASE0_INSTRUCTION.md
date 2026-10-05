# PC-3 Phase-0 Instruction: ACS712-5A Zero-Current Capture (three sensors)

## Цель
Первый реальный zero-current raw capture ACS712-5A на ПК-3: offset и шум
каждого из трёх датчиков (U/V/W) плюс проверка логического маркера PB6, без
изменений прошивки.

## Что нужно на стенде
- STM32G474RE (Nucleo) с прошивкой класса BOARD (текущий артефакт ветки)
- TAO3104A осциллограф (USB подключён к ПК-3)
- **3× ACS712-5A**: U-phase → CH1, V-phase → CH2, W-phase → CH3
- **PB6 → CH4** (trigger/sync marker)
- Zero current: мотор не подключён / инвертор не включён / только 3.3V логика
- **ACS712 Vcc измерить мультиметром** (аргумент `--vcc-mv`, например `5020`)

> **Распиновка изменилась.** CH3 — это ACS712-W, **CH4** — PB6.
> Прежняя схема (PB6 на CH3) недействительна: инструмент читает W на CH3, и
> PB6 на CH3 будет отвергнут проверкой «ACS712-канал выглядит как маркер».

## Софт на ПК-3
```bash
git clone https://github.com/1974Ivanich/OEW.git
cd OEW
git checkout ai2/acs712-scope-foc-map
git pull

python -m venv .venv
.venv\Scripts\activate
pip install -r docs\pc3_package\requirements-acs712-scope.txt
pip install -r docs\pc3_package\requirements-tao3104a.txt
```
Инструменты запускаются из `tools\`. Копий инструментов в `docs\pc3_package\`
больше нет — раньше они разъезжались, и протокол запускал устаревшую версию
без W-канала.

## Запуск Phase-0

```bash
# 1. Self-test (проверка связи с осциллографом)
python tools\scope_acs712_selftest.py --probe

# 2. Phase-0 zero-current capture
# --sens-mv-per-a 185.0 — номинальная чувствительность ACS712-5A
# --vcc-mv 5020        — измеренное питание ACS712, мВ
# --timer-hz 170000000 — TIM1 clock, Гц (170 МГц, STM32G4 APB2)
# --out .tzref01_phase0_pc3 — директория артефактов
python tools\scope_acs712_capture.py ^
    --phase0 ^
    --out .tzref01_phase0_pc3 ^
    --vcc-mv 5020 ^
    --sens-mv-per-a 185.0 ^
    --timer-hz 170000000 ^
    --u-ch CH1 --v-ch CH2 --w-ch CH3 --sync-ch CH4
```

Tool откажется работать при совпадающих каналах (`duplicate scope channels`) —
это защита от чтения PB6 как датчика тока.

## Ожидаемые артефакты
`.tzref01_phase0_pc3/phase0_characterisation.json` содержит:
- `channel_map`: `{"marker":"CH4","u":"CH1","v":"CH2","w":"CH3"}`
- `characterisation`: `CH1`(u), `CH2`(v), `CH3`(w), `CH4`(marker)
- по каждому датчику: `v_mean_mv` (**offset**), `noise_vpp_mv`,
  `noise_vrms_mv`, `zero_noise_pp_ma`, `zero_noise_rms_ma`
- `noise_budget_channels`: `ACS712 channels only (u/v/w); marker channel excluded`
- `phase: "PHASE0"`, `capture_id` (UTC), `profile_id`, `vcc_acs712_mv`,
  `acs712_sensitivity_mv_per_a`

CH4 (PB6) в бюджет шума **не входит** — это логический сигнал 3.3 В, а не ток.
Для него достаточно убедиться, что маркер виден (span ≳ 1500 мВ).

## Что проверить вручную
1. CH1/CH2/CH3 ≈ VCC/2 (например ~2.5 В при 5 В) — на входе ACS712 нет тока
2. CH4 (PB6) — прямоугольник, span ≳ 1500 мВ (маркер реально есть)
3. `noise_vpp_mv` < 50 мВ **для каждого** из CH1/CH2/CH3
4. Нет clipping/overrange ни на одном канале
5. Offsets: записать `v_mean_mv` каждого датчика как v0 для калибровки
   (`tools/acs712_calibration.example.json`)

## Проверка софт-контракта (без железа)
```bash
python tools\acs712_ingest_acceptance.py
```
Это self-test контракта «эмиттер → map_scope_ingest»; он **не имеет
CLI-аргументов** и JSON Phase-0 не проверяет. Проверка JSON — глазами по
чек-листу выше (ранее инструкция предлагала фальшивую команду с
`--calibration`/`--expected-sens`, которая молча игнорировалась).

## Важно
`zero_noise_pp_ma` и `zero_noise_rms_ma` — **номинальная конверсия** через
`--sens-mv-per-a`. Это **не** метрологически квалифицированное измерение тока.
Цель: фактический SNR = smallest_expected_current / noise (см.
`PC3_BENCH_PROTOCOL.md` §4).

## Если что-то не работает
- `python tools\scope_acs712_selftest.py --list` — перечисление USB устройств
- `python tools\tao3104a_cap.py --probe` — диагностика HEAD
- Проверить драйвер Zadig (libusb-win32) для TAO3104A
- Проверить, что scope в режиме "Screen capture" (не "Waveform")
- `ERROR: duplicate scope channels` — четыре канала обязаны быть РАЗЛИЧНЫ

## Передача результатов
Папку `.tzref01_phase0_pc3/` (включая `phase0_characterisation.json`)
передать на ПК-2 (GitHub / shared drive) для следующего этапа.
