# TZ-01 — стендовое evidence: interlock Rs/Ls в `FOC_Start()` (ревизия 2)

**Дата сессии:** 14.09.2026. **Стенд:** ПК-3. **Разбор и приёмка:** ПК-2 (ai2/Hermes).
**Вердикт: PASS** — вся стендовая матрица границ закрыта: 6 из 6 ячеек.

## Артефакты

| Что | Значение |
|---|---|
| Образ | `firmware-tz1-gate-order-production.bin`, 70 536 Б, sha256 `8ddc7f1d4a142bafafbe667d4a3a70a4e46036015c52186b5163da2bf3cdaabb` |
| Источник образа | `edc0f54` (= `origin/main` на момент сборки), `make clean && make`, production/default-deny, **без** `OEW_HS1_COMMISSIONING_RELEASE` |
| Протокол сессии | `C:\campaign_raw\tz1_t1t4_pkg_20260914\SESSION_TZ1_T1_T4_PROTOCOL.md` |
| Сырой UART T1–T4 | `tz1_t1t4_session.log`, 44 779 Б, sha256 `cbfe1be4f5bc463db1493913000f6221ef5e5a5acf48f4a7608317b7a9c6733d` |
| Отчёт T1–T4 | `TZ1_T1_T4_PC3_SESSION_REPORT.md`, 8 991 Б, sha256 `cadb8b92b04aafcf02bba0a8f283e0f0e4c4eb2f4e66648cacfb1eb26366e87b` |
| Скрипт T1–T4 | `tz1_session.py`, sha256 `d00bbcb623cd889ad2cebb45ca9323760058118ee3659408fa4e6dcc567bf9ed` |
| Сырой UART T5 | `tz1_t5_session.log`, 11 621 Б, sha256 `839350FE7295884B…33370C4A` |
| Вердикт T5 | `tz1_t5_verdict.txt`, 941 Б, sha256 `5FF9651AC1FCACA5…C31FE5A8` |
| HV probe T5 | `tz1_t5_hv_probe.txt`, 2 411 Б, sha256 `B85131B0DCC7D518…D981E706` |
| Отчёт T5 | `TZ1_T5_PC3_SESSION_REPORT.md`, 8 048 Б, sha256 `E5018C27FFC88A69…2A808C48` |
| Скрипт T5 | `tz1_session_t5.py`, 4 339 Б, sha256 `4b7de95e1632b926…9559bd08` |
| Расположение | `I:\tz1_t1t4_pkg_20260914\pc3_session\` (копия: `C:\campaign_raw\tz1_t1t4_pkg_20260914\pc3_session\`) |

Хеши возврата T1–T4 и T5 сверены с соответствующими `RETURN_SHA256.txt` при разборе и повторно после копирования — совпадают. T5 выполнен ровно версией `tz1_session_t5.py` с sha256 `4b7de95e1632b926…9559bd08`.

## Образ и перенос вердикта (важно для следующих сессий)

| Что | Образ | Источник | Размер | sha256 |
|---|---|---|---|---|
| Прошит на стенде (T1–T4, T5) | `firmware-tz1-gate-order-production.bin` | `edc0f54` | 70 536 Б | `8ddc7f1d4a142bafafbe667d4a3a70a4e46036015c52186b5163da2bf3cdaabb` |
| Собран из `main` после приёмки | `build/firmware.bin` | `aedd254c` | 70 648 Б | `7f2847eeb544c7d2f9e9b19e7586445e7ca5d00a4e909a2b520d93767d5c16ae` |

Образы **не совпадают**: после стендовой сессии в `main` вошли диагностический фикс CLI
(`src/cli.c` — текст команды `f` по коду возврата) и правки документации. Проверено:
`git diff edc0f54..aedd254c -- src/foc.c src/protect.c src/pwm.c src/adc.c src/adc_dispatch.c`
`src/current_reconstruct.c src/current_map_selector.c` даёт **0 строк** — вся логика
admission/FOC/защиты в `main` идентична той, что стояла на стенде, поэтому поведенческий
вердикт TZ-01 переносится на `main`.

**Правило для следующих сессий:** в отчёте сессии фиксировать sha256 залитого образа —
вердикт переносится только на тот образ, который реально проверялся.

## Метод проверки

T1–T4: независимый построчный разбор сырого UART (469 значимых строк, из них 410 — периодическая телеметрия `@FOC`).
T5: независимый разбор сырого UART (123 значимые строки, 100 строк телеметрии) и повторный запуск собственного `evaluate()` из скрипта T5 на полученном логе. Отчёты ПК-3 как источник доказательств не использовались: проверялись сами строки логов. Условия и ожидания взяты из протокола сессии.

## Таблица проверок

| Проверка | Факт из лога | Итог |
|---|---|---|
| Признак ревизии 2 | попытка `1` без `mp=` → `rc=-6`, а не `-2` | ✅ |
| T1: `mp=9,1000` (Rs < min) | `@MP:OK:Rs=9:Ls=1000:…` → `rc=-6` | ✅ |
| T2: `mp=100001,1000` (Rs > max) | `@MP:OK:Rs=100001:…` → `rc=-6` | ✅ |
| T3: `mp=13000,499` (Ls < min) | `@MP:OK:…:Ls=499:…` → `rc=-6` | ✅ |
| T4: `mp=13000,500001` (Ls > max) | `@MP:OK:…:Ls=500001:…` → `rc=-6` | ✅ |
| C0: `mp=10,500` (обе границы `==min`) | `@MP:OK:Rs=10:Ls=500:…` → `rc=-2` (карта) — гейт пропустил значения | ✅ |
| T5: `mp=100000,500000` (обе границы `==max`) | `@MP:OK:Rs=100000:Ls=500000:…` → `rc=-2` (карта), `rc=-6` отсутствует | ✅ |
| T5: PWM safety | оба `@PWM` → `CR1=224:CCER=0:BDTR=7360:CNT=0`; `0x1CC0` → MOE(bit15)=0 | ✅ |
| Значения доходят до гейта | `@MP:OK` печатает те же значения, которые приняты runtime | ✅ |
| FOC не запускался | T1–T4: `FOC started` 0; T5: `FOC started` 0; `RUN=1` 0 | ✅ |
| Коды отказа | T1–T4: 5×`-6` + 1×`-2`; T5: `-2`; прочих `0/-1/-3/-4/-5` нет | ✅ |
| HV T5 | `@ADC:…:VBUS=32/33` → `@FOC:…:VBUS=3223/3324`; расчёт по divider=125 совпадает, HV не подавалось | ✅ |
| Диапазон входа | `em_stop1=1:em_stop2=1`; safety-состояние сохранено | ✅ |

## T1–T4: выдержка сырого UART

```text
>>> 0
FOC stopped
>>> f
@FAULT:CLEAR:STATUS=1:em_stop1=1:em_stop2=1
>>> p?
@PWM:CR1=224:CCER=0:BDTR=7360:CNT=0
>>> a
@ADC:I1=2043:I2=2070:Ires=0:VBUS=7
>>> 1
FOC start blocked: motor Rs/Ls out of sane range (run autotune, then mp=/mpapply)
@FOC:START:FAIL:rc=-6 (0=OK -1=clock/fault -2=map_unverified -3=calib -4=arm -5=pwm_enable -6=params_out_of_range)
>>> 0
FOC stopped
>>> mp=9,1000
@MP:OK:Rs=9:Ls=1000:Rr=0:Lm=0:Tr=0:Ke=0:p=0:J=0:Kp=27306:Ki=49:Lsig=1000:AP=1
>>> 1
FOC start blocked: motor Rs/Ls out of sane range (run autotune, then mp=/mpapply)
@FOC:START:FAIL:rc=-6 (0=OK -1=clock/fault -2=map_unverified -3=calib -4=arm -5=pwm_enable -6=params_out_of_range)
>>> p?
@PWM:CR1=224:CCER=0:BDTR=7360:CNT=0
>>> 0
FOC stopped
>>> mp=100001,1000
@MP:OK:Rs=100001:Ls=1000:Rr=0:Lm=0:Tr=0:Ke=0:p=0:J=0:Kp=27306:Ki=546125:Lsig=1000:AP=1
>>> 1
FOC start blocked: motor Rs/Ls out of sane range (run autotune, then mp=/mpapply)
@FOC:START:FAIL:rc=-6 (0=OK -1=clock/fault -2=map_unverified -3=calib -4=arm -5=pwm_enable -6=params_out_of_range)
>>> p?
@PWM:CR1=224:CCER=0:BDTR=7360:CNT=0
>>> 0
FOC stopped
>>> mp=13000,499
@MP:OK:Rs=13000:Ls=499:Rr=0:Lm=0:Tr=0:Ke=0:p=0:J=0:Kp=13626:Ki=70997:Lsig=499:AP=1
>>> 1
FOC start blocked: motor Rs/Ls out of sane range (run autotune, then mp=/mpapply)
@FOC:START:FAIL:rc=-6 (0=OK -1=clock/fault -2=map_unverified -3=calib -4=arm -5=pwm_enable -6=params_out_of_range)
>>> p?
@PWM:CR1=224:CCER=0:BDTR=7360:CNT=0
FOC stopped
>>> mp=13000,500001
@MP:OK:Rs=13000:Ls=500001:Rr=0:Lm=0:Tr=0:Ke=0:p=0:J=0:Kp=13653360:Ki=70997:Lsig=500001:AP=1
>>> 1
FOC start blocked: motor Rs/Ls out of sane range (run autotune, then mp=/mpapply)
@FOC:START:FAIL:rc=-6 (0=OK -1=clock/fault -2=map_unverified -3=calib -4=arm -5=pwm_enable -6=params_out_of_range)
>>> p?
@PWM:CR1=224:CCER=0:BDTR=7360:CNT=0
>>> 0
FOC stopped
>>> mp=10,500
@MP:OK:Rs=10:Ls=500:Rr=0:Lm=0:Tr=0:Ke=0:p=0:J=0:Kp=13653:Ki=54:Lsig=500:AP=1
FOC start blocked: current map unverified (load a measured map: mapcap build=<id> or mapload <hex>)
@FOC:START:FAIL:rc=-2 (0=OK -1=clock/fault -2=map_unverified -3=calib -4=arm -5=pwm_enable -6=params_out_of_range)
>>> p?
@PWM:CR1=224:CCER=0:BDTR=7360:CNT=0
>>> 0
FOC stopped
```

## T5: финальная верхняя inclusive boundary

Команды: `0 → f → p? → mp=100000,500000 → 1 → p? → 0`.

Ключевые факты из сырого лога:

```text
>>> mp=100000,500000
@MP:OK:Rs=100000:Ls=500000:…:Kp=13653333:Ki=546133:Lsig=500000:AP=1
>>> 1
FOC start blocked: current map unverified (load a measured map: mapcap build=<id> or mapload <hex>)
@FOC:START:FAIL:rc=-2 (0=OK -1=clock/fault -2=map_unverified -3=calib -4=arm -5=pwm_enable -6=params_out_of_range)
>>> p?
@PWM:CR1=224:CCER=0:BDTR=7360:CNT=0
>>> 0
FOC stopped
```

Независимый `evaluate()` на том же логе дал тот же результат, что и `tz1_t5_verdict.txt`: `PASS: rc=-2 (граница принята, отказал map gate), PWM закрыт`.

## Матрица границ — закрыта полностью

| Ячейка | Параметры | Стенд |
|---|---|---|
| Rs < min | `9` | ✅ `-6` |
| Rs > max | `100001` | ✅ `-6` |
| Ls < min | `499` | ✅ `-6` |
| Ls > max | `500001` | ✅ `-6` |
| Rs == min ∧ Ls == min | `10, 500` | ✅ не `-6` (`-2`) |
| Rs == max ∧ Ls == max | `100000, 500000` | ✅ не `-6` (`-2`), PWM закрыт |

Таким образом проверены обе inclusive boundaries и четыре направления выхода за диапазон. Кроме того, дефолтные параметры ревизии 2 имеют приоритет над map admission (`-6` вместо старого `-2`).

## Ограничения (что эта сессия НЕ доказывает)

1. Калибровка оффсетов (`c`) в T1–T5 не выполнялась; абсолютные значения токов здесь не являются критерием TZ-01.
2. Осциллограмм PWM/апертур нет: критерий «PWM закрыт» доказан регистрами (`CCER=0`, `MOE=0`), а не измерением.
3. Сессия не покрывает `-3` (калибровка), `-4` (ADC arm), `-5` (PWM enable) — они недостижимы без загруженной карты.
4. T5 HV probe является отдельным safety-доказательством отсутствия HV; в основном T5 UART отсутствует команда `a`, поэтому этот probe не смешивается с основным логом.

## Замечания по ходу разбора

- `src/cli.c:174-177`: при ненулевом коде `PROTECT_RequestClear()` ранее печатался один и тот же текст «fault NOT cleared: Vbus/current still out of range». Это диагностический дефект (P2), safety-семантика не затронута; исправление — отдельный пакет, вне вердикта TZ-01.
- В логах есть отдельные строки, где эхо команды вклинилось в периодику — артефакт стримингового монитора; при автоматическом разборе нужна терпимость к этому.
- `BDTR=7360` (`0x1CC0`): `DTG[7:0]=0xC0` → dead-time 192 тика, совпадает с `deadtime_ticks=192` в identity-контракте.
- `@MP:OK` при `Ls=500001` показывает `Kp=13653360` — физически бессмысленные PI-коэффициенты; гейт как раз и не даёт их применить.

## Ссылки

- `docs/ТЗ  interlock параметров двигателя в FOC_Start().md` — ТЗ-01 с §4 (инвариант дефолта), §9 (ревизия 2: контракт приоритета admission).
- `src/foc.c` — порядок гейтов `FOC_Start()`; `tests/foc_start_gate_test.c` — матрица A0/A/B/C/D/E.
