# ТЗ: MapCapture fault-detail для no-HV test № 2

**Статус:** реализуется по прямому разрешению пользователя от 25.08.2026.
**Область:** `src/map_capture.c/.h`, UART-строка `mapcap status`, host-тесты MapCapture/CLI.
**Не входит:** изменение PWM/ADC конфигурации, `.ioc`, FOC/V/f, normal-control admission, energise и Stage A.

## Цель

Устранить неоднозначность `MAP_CAPTURE_LIMIT_EXCEEDED (-12)` и
`MAP_CAPTURE_ADC_FAULT (-11)` для no-HV test № 2. Нынешний terminal status
указывает класс отказа, но не позволяет отличить VBUS-low от I1/I2 limit или
непригодного ADC frame. Следовательно, host-автоматизация не должна принимать
один только `term=-11/-12` за доказательство корректной реакции на отсутствие
VBUS.

## Контракт

Вводится `MapCaptureFaultDetail`, который фиксирует **первую детерминированную
причину terminal stop**. `MapCaptureStatus` и порядок безопасного остановa
(`stop_service_pwm`, ADC stop, control admission false, central latch) остаются
без изменений.

| Detail | Условие | Сохраняемые frame evidence |
|---|---|---|
| `NONE` | Нет terminal fault detail | Нули |
| `ADC_FRAME_NULL` | `MapCapture_OnAdcFrame(NULL)` | Нули |
| `ADC_STATUS_INVALID` | `frame->status != ADC_FRAME_WINDOW_INVALID` | Raw/engineering кадр |
| `ADC_SECTOR_MISMATCH` | Несовпадение `tim1_sector` | Raw/engineering кадр |
| `ADC_WINDOW_MISMATCH` | Несовпадение `sample_window` | Raw/engineering кадр |
| `I1_LIMIT` | `abs(idc1_ma) > max_abs_shunt_ma` | Raw/engineering кадр |
| `I2_LIMIT` | I1 в норме, `abs(idc2_ma) > max_abs_shunt_ma` | Raw/engineering кадр |
| `VBUS_LOW` | I1/I2 в норме, `vbus_mv < min_vbus_mv` | Raw/engineering кадр |
| `VBUS_HIGH` | Предыдущие проверки в норме, `vbus_mv > max_vbus_mv` | Raw/engineering кадр |

Приоритет сравнения фиксирован таблицей, поэтому одна и та же входная рамка
всегда получает один и тот же detail. Первая причина не перезаписывается.

`MapCaptureStats` дополняется detail и последним terminal ADC evidence:
`raw_vbus`, `vbus_mv`, `idc1_ma`, `idc2_ma`, `adc_status`, `tim1_sector`,
`sample_window`. Поля являются read-only диагностикой; они не могут открывать
control admission или изменять защиту.

`mapcap status` добавляет поля после существующего контракта:

```text
@MC:STATUS:state=…:term=…:cap=…:frames=…:dropped=…:periods=…:avail=…:
detail=…:raw_vbus=…:vbus_mv=…:i1_ma=…:i2_ma=…:adc_status=…:sector=…:window=…
```

Существующие первые семь полей не переименовываются и не меняют порядок.

## Приёмка

1. Host-тесты проверяют раздельно `I1_LIMIT`, `I2_LIMIT`, `VBUS_LOW`,
   `VBUS_HIGH`, непригодный frame и null frame.
2. Каждый тест подтверждает неизменный `terminal_status`, детерминированный
   `fault_detail` и сохранённый ADC evidence.
3. UART/CLI test-double отражает расширенный status-контракт.
4. `make test` и production build должны пройти до публикации.
5. Эта ветка **не разрешает физический test № 2**. Скрипт automation обновляется
   отдельным последующим пакетом, чтобы использовать новый contract и выдавать
   двухфазный verdict.
