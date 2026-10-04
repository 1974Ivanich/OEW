# Идентичность и baseline — заполнено по фактам прогона 2026-10-04 (ПК-3, COM4)

Копия `IDENTITY_AND_BASELINE.md` пакета ред. 2 с фактическими значениями стенда.
Сырые логи: `docs/evidence/incident_20261004_hwbreak/01_baseline_raw.log`,
`…/05_step0_result.json`, `…/04_precheck_report.txt`, `docs/evidence/scan_20261004_run1/*`.

## Плата / образ

| что | значение |
|---|---|
| образ (файл / sha256, если известен) | по UART sha256 образа не читается; дамп Flash с платы: `backup/flash_20261004_193226.bin`, sha256 `2cbab38a9182a48e1537bd5dfeb16888772514cdf987c37cc6a5fb93659329e2` (тот же, что 03.10 — образ не менялся); объявленный источник регистров — `ab1274d` |
| чем прошит, когда | не в этой сессии (перепрошивка не выполнялась) |
| `sysinfo` (целиком) | `@SYS:CLK=170000000:PSC=16:TCLK=10000000:PLLCFGR=0x01005532:OVR=0:JEOS=0:TO=0:JQOVF=0:uart_drp=0:uart_trunc=0` |
| `a?` | до калибровки: `@ADC:STATUS:offset_i1=0:stream=0`; после `c`: `offset_i1=2038` |
| `pdump` | `@PWM:FULL:SYS=170000000:CFGR=0x0000000F:T1:PSC=16:ARR=999:CCR=0,0,0:BDTR=0x00001CC0:CCER=0x00000000:CR1=0x000000E0:CNT=0:T8:PSC=16:ARR=999:CCR=0,0,0:BDTR=0x00001CC0:CCER=0x00000000:CR1=0x000000E0:CNT=0` |
| `TOOLS/check_step0.py` | `verdict: PASS (rc=0)`, 29/29 (`05_step0_result.json`, `source: port:COM4@115200`) |
| `p?` | `@PWM:CR1=224:CCER=0:BDTR=7360:CNT=0` (CR1=224=0xE0 → CMS=11, CEN=0; BDTR=7360=0x1CC0 → MOE=0) |
| ответ на `1` (дословно) | `FOC start blocked: motor Rs/Ls out of sane range (run autotune, then mp=/mpapply) @FOC:START:FAIL:rc=-6 (0=OK -1=clock/fault -2=map_unverified -3=calib -4=arm -5=pwm_enable -6=params_out_of_range)` |
| `breakdiag` | до подачи питания: `@BRK:valid=0`; после подачи 60 В: `@BRK:valid=1:seq=1:src=TIM1:cyc=0:sr=81,81:sd=1,1:bd=1CC0,1CC0:ce=0,0:cnt=0,0` |

`dumpa` (инвертор обесточен, до подачи питания):

```
@ADUMP:SQR1=0x00000000:CFGR=0x80000000:SMPR1=0x00038FC0:JSQR=0x00A18482:DIFSEL=0x00000000:CR=0x10000001:ISR=0x00000001:DR=0x0000:JDR1=0x0000:JDR2=0x0000:JDR3=0x0000:JDR4=0x0000:ADC1_CR=0x10000001:ADC1_ISR=0x00000001
```

## Baseline-снимок (инвертор обесточен)

`dump`:

```
@PWM:DUMP:PSC=16:ARR=999:BDTR=0x00001CC0:CR1=0x000000E0:CR2=0x00000020:CCER=0x00000000
```

`dumpa` — см. выше (получен в этой же сессии, `01_baseline_raw.log`).

## Декод CMS/MMS/URS/RCR

| регистр | биты | значение | вывод |
|---|---|---|---|
| TIM1->CR1 | 6:5 | `0xE0` → 0b11 | CMS = 3 (center-aligned) |
| TIM1->CR2 | 6:4 | `0x20` → 0b010 | MMS = 2 (010, UEV → TRGO) |
| TIM1->CR1 | 8 | `0xE0` → 0 | URS = 0 |
| TIM1->RCR | — | «не печатается» | RCR = 1 (якорь: исходник образа, `src/pwm.c:273`; `dump` не печатает RCR) |

## Шкала метрики

```
py -3 TOOLS/calc_expected.py --clk 170000000 --psc 16 --arr 999 --cms 3 --rcr 1 \
    --tclk 10000000 --window 2.0 --mms 2 --urs 0
```

Фактические: f_pwm = **5000.000** Гц, f_JEOS = **5000.000** Гц, N_expected = **10000**,
`RESULT` = **PASS** (TCLK cross-check OK, guard `f_pwm == 5000 Hz`),
формула = `f_cnt=CLK/(PSC+1); f_pwm=f_cnt/(2*(ARR+1)); f_UEV=2*f_pwm; f_JEOS=f_UEV/(RCR+1); RCR=1`

## Проверки перед клетками

| проверка | результат |
|---|---|
| `sysinfo` печатает `JEOS=` | OK (`JEOS=0`) |
| `i=2000,0` отвечает `@I:OK:Id=…:Iq=…` | OK (`@I:OK:Id=2000:Iq=0`) |
| `sysinfo` печатает `OVR=` (для L3) | OK (`OVR=0`) |
| `enc` читается (`@ENC:angle=…`, 14 бит), исходный угол записан | OK (`angle=5144`, `err=0`; в `precheck`) |
| `a?` до скана (база для дрейфа нулей) | OK, снят (`offset_i1=0` до `c`, `2038` после) |
| FOC остаётся остановленным (`RUN=0`), латчей нет | OK (`RUN=0`, `FAULT=0:FAULT_R=0`) |
| вердикт pre-cell | `PASS` (8/8, `04_precheck_report.txt`) |

## Прочее

- лимит источника тока: не зафиксирован (стенд владельца); питание: подано 60 В,
  измерено `@ADC:VBUS=619` raw ≈ 62.3 В и `@FOC:VBUS=62353` мВ
- чем измеряется VBUS (прибор): ADC-канал VBUS платы (делитель 1:125, `src/adc.h:20`),
  внешний прибор не использовался
- оператор / время: владелец стенда (подача питания) / 2026-10-04, ~19:32–19:52
- версия скрипта скана (sha256 `TOOLS/scan_48cell.py`): `0d76f5fd49bcd8909f49d3c75c54d0ff77aee0911adb97d8bcdc31cc0d66f612`
- ревизия пакета: ред. 2 (см. `SHA256SUMS`, `CORRECTIONS_v2.md`)

## Итог по живому блоку амплитуд

0 клеток из 48: `run1` остановлен автостопом на первой клетке (`rc=-6`),
подробности — `docs/evidence/scan_20261004_run1/REPORT.md` и
`docs/evidence/incident_20261004_hwbreak/incident.md`.
