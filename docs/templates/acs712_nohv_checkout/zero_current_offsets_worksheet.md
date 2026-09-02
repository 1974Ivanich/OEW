# Рабочий лист zero-current offsets ACS712

| Параметр | Измерено (мВ) | Ожидаемо (мВ) | Разница | Примечание |
|---|---|---|---|---|
| Vcc (фактическое питание датчиков) | | Vcc/2 * 2 | | |
| v0_U (zero-current output, фаза U) | | Vcc/2 | | |
| v0_V (zero-current output, фаза V) | | Vcc/2 | | |
| Шум U (peak-to-peak) | | < 110 мВ pp | | |
| Шум V (peak-to-peak) | | < 110 мВ pp | | |

Оператор: _________________   UTC: _________________   DMM/scope ID: _________________

## Проверка перед записью calibration JSON

- [ ] Измерения выполнены при физически отключённом DC-link и zero motor current.
- [ ] 0_U и 0_V находятся в пределах Vcc/2 ± 200 мВ.
- [ ] Шум не превышает ~110 мВ pp (типично ~30 мВ pp у ACS712-20A).
- [ ] Нет rail/saturation (значения не близки к 0 мВ или Vcc).

После PASS перенесите 0_U, 0_V и Vcc в calibration/acs712_calibration.json.
