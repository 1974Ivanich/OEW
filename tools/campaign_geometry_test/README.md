# Synthetic geometry regression campaign

Это **синтетическая software-test fixture**, а не BOAR measurement evidence и не основание для safety approval или firmware map loading.

Генерация:

```bash
python tools/generate_geometry_test_campaign.py tools/campaign_geometry_test
```

Свойства fixture:

- 12 строк `sector/window`;
- 8 samples на каждую строку;
- modulation samples в window 0: `7000..9500 Q15`;
- modulation samples в window 1: `10500..13800 Q15`;
- deterministic linear current model;
- достаточная численность для solver-compatible dataset validation.

Проверка interpolation harness:

```bash
python tools/map_geometry_interpolation_analysis.py \
  tools/campaign_geometry_test /tmp/geometry_test_report.json
```

Ожидаемый результат: `PASS` для текущего empirical harness и 12 строк. Это не доказывает корректность production SVPWM geometry: semantic sector predicate, wire-format revision, firmware admission и hardware campaign остаются отдельными gates.
