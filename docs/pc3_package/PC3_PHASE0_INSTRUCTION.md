# PC-3 Phase-0 Instruction: ACS712-5A Zero-Current Capture

## Цель
Получить первый реальный ACS712-5A zero-current raw capture на ПК-3 и из него вычислить шум, не меняя прошивку и tooling.

## Что нужно на стенде
- STM32G474RE (Nucleo) с прошивкой `firmware-tz2-mapcap-main-b39b3b6.bin` (уже на плате)
- TAO3104A осциллограф (USB подключён к ПК-3)
- 2× ACS712-5A (U-phase, V-phase) — подключены к scope CH1, CH2
- PB6 → scope CH3 (trigger/sync marker)
- Zero current condition: мотор не подключен / инвертор не включён / только 3.3V логика платы

## Софт на ПК-3
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-acs712-scope.txt
pip install -r requirements-tao3104a.txt
```

## Запуск Phase-0

```bash
# 1. Self-test (проверка связи с осциллографом)
python tools/scope_acs712_selftest.py --device TAO3104A

# 2. Phase-0 zero-current capture
# --sens-mv-per-a 185.0 — номинальная чувствительность ACS712-5A
# --duration 5 — секунд захвата для noise statistics
python tools/scope_acs712_capture.py \
    --device TAO3104A \
    --phase0 \
    --sens-mv-per-a 185.0 \
    --duration 5 \
    --output phase0_acs712_5a_pc3.json

# 3. Acceptance test (проверка capture quality)
python tools/acs712_ingest_acceptance.py \
    --input phase0_acs712_5a_pc3.json \
    --expected-sens 185.0
```

## Ожидаемые артефакты
- `phase0_acs712_5a_pc3.json` — содержит:
  - `noise_vpp_mv` (per channel)
  - `noise_vrms_mv` (per channel)
  - `zero_noise_pp_ma` (per channel, using --sens-mv-per-a)
  - `zero_noise_rms_ma` (per channel)
  - `sampling_window_us`
  - `trigger_config`
  - `capture_id` / `profile_id`
  - `acs712_sensitivity_mv_per_a` = 185.0
  - `map_control_range_a` = {1.0, 3.0}
  - `below_map_control` = "V/F"

## Что проверить вручную
1. Zero current действительно zero: scope показывает ~VCC/2 на CH1/CH2
2. PB6 marker виден на CH3 (square wave, TIM1 TRGO)
3. Capture duration ≥ 5 секунд
4. No overrange / clipping на scope

## Если что-то не работает
- `python tools/tao3104a_cap.py --device TAO3104A --test-connection` — диагностика USB
- Проверить драйвер Zadig (libusb-win32) для TAO3104A
- Проверить, что scope в режиме "Screen capture" (не "Waveform")

## Передача результатов
Файл `phase0_acs712_5a_pc3.json` передать на ПК-2 (GitHub / shared drive) для следующего этапа.
