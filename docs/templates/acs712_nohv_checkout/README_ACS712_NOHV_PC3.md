# Пакет ПК-3 — no-HV checkout ACS712 scope-reference

Назначение: проверить проводку двух датчиков ACS712-20A (CH1=фаза U, CH2=фаза V) и измерить их zero-current output offsets (0_U, 0_V) при обесточенном моторе/DC-link. Полученные значения записываются в cs712_calibration.json и используются later при ingest mV-CSV для grid-кампании BOAR.

Классификация: **no-HV / de-energized checkout**. Запрещены подача DC-link, команды mcarm, mapcap run/drain/uild, FOC/V/f/autotune, а также любое energizing управление мотором.

## Предварительные проверки проводки (operator done)

- [ ] ACS712 U включён последовательно в фазный провод U (не в DC-link shunt).
- [ ] ACS712 V включён последовательно в фазный провод V.
- [ ] CH1 осциллографа — выход ACS712 U; CH2 — выход ACS712 V.
- [ ] Питание датчиков — **внешние 5.0 В** (не 3.3 В с J2).
- [ ] GND датчиков и GND осциллографа общие; силовая сторона ACS712 остаётся гальванически развязанной.
- [ ] Выходной фильтр CF ≤ 1 нФ; **47 нФ не установлен**.

## Hard gates перед включением питания

| ID | Gate | PASS критерий | Evidence / подпись |
|---|---|---|---|
| G-01 | DC-link | Обе шины физически отсоединены; DMM < 1 В каждая | |
| G-02 | PC4 | Нет внешнего источника/имитатора VBUS на PC4 | |
| G-03 | Aux supply | Толькоapproved low-voltage aux: Nucleo USB/ST-Link и 5 В для ACS712 | |
| G-04 | Emergency | Оператор может обесточить aux по локальной процедуре | |
| G-05 | Firmware identity | Свежий main, green CI, default-deny production образ | SHA/run: |
| G-06 | Command boundary | Только sysinfo, p?, pdump, c, ; никаких energizing команд | |

> **PC-3 reality check (2026-09-04):** the production default-deny image does **not** expose `mapcap status` / `@MC:STATUS` — those commands are compiled only when `OEW_MAP_CAPTURE` is defined (`main.c` line 493). This is expected and acceptable for this de-energized checkout, because `G-06` forbids map-capture commands and only permits non-energizing observation commands (`sysinfo`, `p?`, `pdump`, `c`).

## Компоновка кампании (вне Git)

`	ext
D:\campaign_raw\acs712_nohv_YYYYMMDDTHHMMSSZ\
├── identity/
│   ├── source_sha.txt
│   ├── ci_run_url.txt
│   └── flash_verify.log
├── dmm/
│   └── zero_current_offsets.md
├── scope/
│   └── scope_zero.csv
├── calibration/
│   └── acs712_calibration.json
├── observations/
│   └── wiring_check.md
└── summary/
    └── acs712_nohv_summary.md
`

## Критическое расхождение: firmware identity на ПК-3

ПК-3 отчитался о следующем состоянии (2026-09-04):

- Текущая ветка на ПК-3 — `ai2/sd-monitor-only` (`cfcbeae`), а не `main`.
- Коммит `6955672` (упомянутый в упаковке приёмки) **не существует** ни локально, ни на `origin/main` (`6a91312`), ни на GitFlic.
- В рабочей копии ПК-3 лежат незакоммиченные zip-пакеты приёмки и локальная правка `scripts/cubemx_check_script.txt`.
- `build/firmware.bin` — сборка от 01.09 образа `sd-monitor-only`, не `main`.

**Решение по G-05:** перед физическим no-HV checkout ПК-3 должен перейти на свежий `origin/main`, выполнить `make clean && make` (default-deny production образ) и зашить его. `ai2/sd-monitor-only` уже влита в `main` (merge `af03126`), поэтому образ из `main` содержит те же SD-monitor изменения, но даёт известный SHA/CI/run для evidence. Существующий `build/firmware.bin` от 01.09 **не принимается** как identity evidence.

Если по какой-либо причине пересборка/перепрошивка с `origin/main` невозможна, checkout блокируется на G-05 и Phase 1 не может быть признан PASS.

## Порядок выполнения

1. **Hard gates G-01…G-06** — все PASS, подписи оператора.
2. **Flash** default-deny production образа из зелёного CI main.
3. **UART capture**: откройте фактический MCU VCP и начните непрерывную запись.
4. Отправьте sysinfo, p?, pdump — проверьте identity и default-deny.
5. Отправьте c один раз — зафиксируйте offset_i1/offset_i2/offset_ires.
6. Отправьте  десять раз — зафиксируйте raw ADC samples (I1/I2/Ires/VBUS). Ожидается шум около zero-current, без saturation/rail.
7. **Zero-current offsets ACS712**: при отключённом моторе (zero phase current) измерьте DC-уровень выхода каждого ACS712:
   - DMM (DC) на выходе U и V;
   - осциллограф: усреднённое значение на плато (не пик-шум).
8. Запишите измеренные 0_U, 0_V и фактический Vcc в calibration/acs712_calibration.json. Файл-шаблон лежит в этом каталоге (cs712_calibration_template.json).
9. Скопируйте CSV-шаблон scope_zero_template.csv в scope/scope_zero.csv и заполните измеренные мВ. 
ef_w_mv оставьте пустым (KCL). scope_qualified=0, т.к. это обесточенная запись, не апертура MapCapture.
10. Проверьте JSON: python -m json.tool calibration/acs712_calibration.json.
11. Сверьте, что измеренные 0_* близки к Vcc/2 (типично 2500 мВ при Vcc=5 В). Если расхождение > ±200 мВ или шум аномален — остановитесь, проверьте проводку/питание/CF.
12. Заполните summary/acs712_nohv_summary.md, operator initials.

## Критерии PASS/FAIL

| Domain | PASS | FAIL / BLOCKED |
|---|---|---|
| Firmware identity | sysinfo отвечает, SHA/CI/run записаны | stale/unknown response, незаверенный образ |
| PWM default-deny | p?/pdump показывают MOE=0, нет active PWM | energized state |
| ADC baseline | c даёт все три offsets, нет @ADC:CAL:FAIL | fail/timeout/missing |
| ACS712 zero offsets | 0_U и 0_V в пределах Vcc/2 ± 200 мВ, шум разумный | rail, >200 мВ offset, >200 мВ pp noise, нестабильность |
| No-HV boundary | DC-link <1 В, нет PC4 source, нет energizing команд | любое нарушение |

## Следующий шаг

Этот пакет даёт cs712_calibration.json, необходимый для запуска 	ools/map_scope_ingest.py --calib ... при future grid-кампании BOAR. Сам по себе он **не** разрешает Test №3 MapCapture, Stage A или energized characterization — для них требуется отдельное ТЗ, G0 approval и pre-flight.

## Ссылки

- TZ_ACS712_SCOPE_REFERENCE.md — ТЗ на scope-reference.
- 	ools/map_scope_ingest.md — формат mV-CSV и calibration JSON.
- docs/templates/test3_nohv_campaign/scope/scope_region_template_acs712.csv — шаблон mV-CSV grid-кампании.
