# PC-3 Phase-0 Instruction: ACS712-5A Zero-Current Capture

## Цель
Получить первый реальный ACS712-5A zero-current raw capture на ПК-3 и из него вычислить шум, не меняя прошивку и tooling.

## Что нужно на стенде
- STM32G474RE (Nucleo) с прошивкой `firmware-tz2-mapcap-main-b39b3b6.bin` (уже на плате)
- TAO3104A осциллограф (USB подключён к ПК-3)
- 2× ACS712-5A (U-phase, V-phase) — подключены к scope CH1, CH2
- PB6 → scope CH3 (trigger/sync marker)
- Zero current condition: мотор не подключен / инвертор не включён / только 3.3V логика платы
- **ACS712 Vcc измерить мультиметром** (параметр `--vcc-mv`, например `5000` для 5.0 В)

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
python tools/scope_acs712_selftest.py --probe

# 2. Phase-0 zero-current capture
# --sens-mv-per-a 185.0 — номинальная чувствительность ACS712-5A
# --vcc-mv 5000 — измеренное напряжение питания ACS712, мВ
# --timer-hz 170000000 — TIM1 clock, Гц (170 МГц, STM32G4 APB2)
# --out .tzref01_phase0_pc3 — директория для артефактов
python tools/scope_acs712_capture.py \
    --phase0 \
    --out .tzref01_phase0_pc3 \
    --vcc-mv 5000 \
    --sens-mv-per-a 185.0 \
    --timer-hz 170000000 \
    --u-ch CH1 \
    --v-ch CH2 \
    --sync-ch CH3

# 3. Acceptance test (проверка capture quality)
# артефакт: .tzref01_phase0_pc3/phase0_characterisation.json
python tools/acs712_ingest_acceptance.py \
    --calibration .tzref01_phase0_pc3/phase0_characterisation.json \
    --expected-sens 185.0
```

## Ожидаемые артефакты
`.tzref01_phase0_pc3/phase0_characterisation.json` — содержит:

```json
{
  "idn": "...",                       // IDN осциллографа
  "scope_head": {...},               // полный HEAD
  "declared_timer_hz": 170000000,   // TIM1 clock
  "channel_map": {
    "marker": "CH3",                // PB6 sync
    "u": "CH1",                     // ACS712 U
    "v": "CH2"                      // ACS712 V
  },
  "vcc_acs712_mv": 5000,           // измерено мультиметром
  "acs712_sensitivity_mv_per_a": 185.0,
  "map_control_range_a": {"min": 1.0, "max": 3.0},
  "below_map_control": "V/F",
  "characterisation": {
    "CH1": {
      "noise_vpp_mv": ...,           // мВ peak-to-peak
      "noise_vrms_mv": ...,         // мВ RMS
      "zero_noise_pp_ma": ...,       // мА pp (через 185 mV/A)
      "zero_noise_rms_ma": ...,     // мА RMS
      "window_duration_s": ...,       // длительность захвата, с
      "sample_rate_hz": ...
    },
    "CH2": {...},
    "CH3": {...}
  },
  "phase": "PHASE0",
  "capture_id": "20260925T123456Z",  // UTC timestamp
  "profile_id": "BOAR_v2_PWM_OEW_BOARD_REVISION_7"
}
```

## Что проверить вручную
1. Zero current: scope показывает ~VCC/2 на CH1/CH2 (2.5 В при 5 В питании)
2. PB6 marker виден на CH3 (square wave, TIM1 TRGO)
3. `noise_vpp_mv` < 50 мВ для обоих каналов — ожидаемый baseline
4. No overrange / clipping на scope

## Важно
`zero_noise_pp_ma` и `zero_noise_rms_ma` — это **номинальная конвертация** через 185 mV/A.
Это **не** является метрологически квалифицированным измерением тока.
Цель: получить фактический SNR = smallest_expected_current / noise.

## Если что-то не работает
- `python tools/scope_acs712_selftest.py --list` — перечисление USB устройств
- `python tools/tao3104a_cap.py --probe` — диагностика HEAD
- Проверить драйвер Zadig (libusb-win32) для TAO3104A
- Проверить, что scope в режиме "Screen capture" (не "Waveform")

## Передача результатов
Папку `.tzref01_phase0_pc3/` (включая `phase0_characterisation.json`)
передать на ПК-2 (GitHub / shared drive) для следующего этапа.
