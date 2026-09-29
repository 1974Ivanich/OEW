# ТЗ: автоматизированная кампания калибровки шунтов на ПК-3 (shunt_cal_campaign)

Ветка: `ai2/shunt-cal-campaign-v1` (поверх `ai2/current-shunt-cal-v1`, `2e2ebf8`).
Инструмент: `tools/shunt_cal_campaign.py` (stdlib-only; pyusb/pyserial — только
на стенде). Firmware не трогается вовсе.

## 1. Роли приборов (неприкосновенны)

```text
TAO3104A (CH1 → шунт Inv1, CH2 → шунт Inv2)  =  ЭТАЛОН: I_ref = V_shunt / R_shunt
STM32 raw_i1/raw_i2 (окна ci 0/1/2 + @ADC)   =  НЕИЗВЕСТНАЯ измеряемая величина
```

Firmware-коэффициенты кампания не меняет. Цепочка решений:
`REFERENCE DATASET → auto-fit (ci_fit.py как единая реализация) → вердикт в
campaign_report.json → независимая перепроверка (по желанию) → только после PASS
отдельное ТЗ → firmware calibration`.
Ручного шага fit между acquisition и решением нет: кампания сама экспортирует
`points.csv` и печатает вердикт (условия — §5a).

## 2. Capability/readback gate

SET-команды SCPI на V3.0.0 подтверждённо игнорируются
(`tools/OWON_TAO3104A_PROTOCOL.md`, проверено с power-cycle), поэтому настройку
делает оператор, а инструмент **верифицирует её readback'ом**:

1. `*IDN?` — отвечает и содержит `TAO3104A`;
2. HEAD читается, `DATALEN > 0`, `IDN` внутри HEAD совпадает с `*IDN?`;
3. CH1/CH2 присутствуют и `DISPLAY=ON`;
4. readback ожиданий: SCALE (допуск %, default 15), PROBE, COUPLING;
5. пробное чтение waveform обоих каналов: полный DATALEN, не все-нули.

Любой отказ → вердикт **`SCOPE_CONTROL_BLOCKED`**, `campaign_report.json`
записан, записи кампании НЕ создаются. Это закрывает урок `HEAD/CH1`:
«прибор отвечает» ≠ «прибор отдаёт корректные waveform».

## 3. Фазы

**A — zero (PWM OFF, тока нет):** waveform CH1/CH2 + `ci 0` (оба zero-окна) +
телеметрия `@ADC`. Дают offset_i1/offset_i2 и noise (pp/rms по окну).

**B — контролируемые точки (default 500…3000 мА, задаёт оператор):** на каждой
точке — waveform CH1/CH2 + `ci 1` + `ci 2` + `@ADC`. Inv1/Inv2 снимаются
одновременно → сразу видно межинверторное расхождение.

Запись точки (в `campaign_report.json`): I_ref по среднему окна каждого канала,
статистика окна (mean/median/rms/pp), конфигурация прибора (SCALE/PROBE/COUPLING/
OFFSET, timebase), `zero_window`/`raw_i1_window`/`raw_i2_window`/телеметрия,
advisory gain (pre-fit, не вердикт).

## 4. Артефакты и provenance

```
SHUNTCAL/
  campaign_report.json     записи + гейт + вердикт + dataset_crc32
  scope_CH1_<point>.csv    сырой waveform (t_s, volts, code) — полный, не «итог»
  scope_CH2_<point>.csv
  uart_session.log         сырой обмен с STM32
  points.csv               dataset для fit: channel,point,vshunt_mv,raw_avg
                           (экспорт после dataset_crc32: производный, не в CRC)
provenance: IDN/MODEL прибора, CODE_0V, rshunt, firmware_sha256, map_crc32,
host, timestamp каждой точки, dataset_crc32 (по waveform CSV + логу)
```

Отчёт пишется после каждой точки (crash-resilient); CRC — по неизменяемым
артефактам (сам отчёт исключён: он дописывает себя после подсчёта).

## 5. Порядок на стенде (ПК-3)

```
прошивка с ci (ai2/current-shunt-cal-v1) и калиброванным cv
    → щупы на шунты, ручная настройка TAO (SCALE/PROBE/COUPLING, DISPLAY=ON)
    → py -3 tools/shunt_cal_campaign.py campaign --out SHUNTCAL --port COM4
      --expect-scale-ch1 100mV --expect-scale-ch2 100mV --expect-probe-ch1 1X ...
    → gate PASS → фаза A (Enter) → точки (подача тока по схеме ПК-3, Enter)
    → SHUNTCAL/ → Auto-fit: вердикт в консоли и в campaign_report.json
      (points.csv + offset/sign/gain/R²/остатки по каналам)
    → независимая перепроверка (по желанию): py -3 tools/ci_fit.py SHUNTCAL/points.csv
```

## 5a. Вердикты авто-fit (единственный источник решения)

| Вердикт | Условие |
|---|---|
| `PASS` | по всем фактически участвующим каналам: R² ≥ 0.999, \|intercept\| ≤ 2 raw, масштаб определён (gain > 0, конечный uv_per_a), отклонение от прошивочного номинала 63000 µV/A ≤ 20 % |
| `NEED_GAIN_CHANGE` | модель чистая, но отклонение масштаба > 20 % (сигнал для решения по gain) |
| `SUSPECT` | R²/intercept вне допуска, масштаб не определён (gain ≤ 0 / nan) или канал вырожден: одна точка, одинаковые I_ref либо **нулевая дисперсия raw при меняющемся I_ref** (залипший/мёртвый канал) |
| `FIT_INCOMPLETE` | данных нет: все точки пропущены или нет окон ci |

Набор каналов = фактически присутствующие в `points.csv`: кампания только на
одном инверторе не обязана иметь второй канал для вердикта.

Fail-closed правило: канал, чей raw не меняется при разных I_ref (окна ci отдают
одно и то же значение), **никогда** не даёт `PASS` — это ошибка канала, а не
«идеальная модель». Тот же запрет действует в CLI `ci_fit.py` (rc=1).

## 6. Что НЕ делает пакет

* не меняет firmware и `calc_dc_shunt_ma`; не «настраивает» осциллограф SET-ами
  (прибор их игнорирует — верификация readback'ом);
* не доказывает пригодность ADC sampling window для closed-loop FOC — кампания
  калибрует transfer function `shunt → ADC raw`; проверка мгновенного съёма
  тока в нужной PWM-фазе — отдельный, более узкий gate;
* не решает вопрос линейности: если остаток fit растёт с током — это вопрос
  аналогового тракта (R29/R33, gain 2.1), решается по данным, не этим пакетом.

## 7. Приёмка

1. pytest-набор: гейт-отказы (8 причин блокировки), PASS-путь, модель вольт на
   живой калибровочной точке (code 222 → +0.3300 V), SI-парсер (ловушка 1MSa/s),
   записи/оркестрация на фейках, парсинг `@CI:*`/`@ADC` — зелёные.
2. Смоук без прибора: rc=3, `SCOPE_CONTROL_BLOCKED`, 0 записей.
3. fail-open gate: залипший канал (raw = const при разных I_ref) не даёт `PASS`
   — unit-тесты + `ci_fit.py` по такому CSV возвращает rc=1, а не «замечаний нет».
4. CI `build-test` зелёный (numpy/pyusb в CI-джобе не требуются).
