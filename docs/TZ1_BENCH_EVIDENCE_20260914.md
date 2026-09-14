# TZ-01 — стендовое evidence: interlock Rs/Ls в `FOC_Start()` (ревизия 2)

**Дата сессии:** 14.09.2026. **Стенд:** ПК-3. **Разбор и приёмка:** ПК-2 (ai2/Hermes).
**Вердикт: PASS** для проверенной части матрицы границ (5 из 6 ячеек).

## Артефакты

| Что | Значение |
|---|---|
| Образ | `firmware-tz1-gate-order-production.bin`, 70 536 Б, sha256 `8ddc7f1d4a142bafafbe667d4a3a70a4e46036015c52186b5163da2bf3cdaabb` |
| Источник образа | `edc0f54` (= `origin/main` на момент сборки), `make clean && make`, production/default-deny, **без** `OEW_HS1_COMMISSIONING_RELEASE` |
| Протокол сессии | `C:\campaign_raw\tz1_t1t4_pkg_20260914\SESSION_TZ1_T1_T4_PROTOCOL.md` |
| Сырой UART | `tz1_t1_t4_session.log`, 44 779 Б, sha256 `cbfe1be4f5bc463db1493913000f6221ef5e5a5acf48f4a7608317b7a9c6733d` |
| Отчёт ПК-3 | `TZ1_T1_T4_PC3_SESSION_REPORT.md`, 8 991 Б, sha256 `cadb8b92b04aafcf02bba0a8f283e0f0e4c4eb2f4e66648cacfb1eb26366e87b` |
| Скрипт сессии | `tz1_session.py`, sha256 `d00bbcb623cd889ad2cebb45ca9323760058118ee3659408fa4e6dcc567bf9ed` |
| Расположение | `I:\tz1_t1t4_pkg_20260914\pc3_session\` (копия: `C:\campaign_raw\tz1_t1t4_pkg_20260914\pc3_session\`) |

Хеши возврата сверены с `RETURN_SHA256.txt` при разборе и повторно после копирования — совпадают.

## Метод проверки

Независимый построчный разбор сырого UART (469 значимых строк, из них 410 — периодическая
телеметрия `@FOC`). Отчёт ПК-3 как источник доказательств не использовался: проверялись сами
строки лога. Условия и ожидания взяты из протокола сессии.

## Таблица проверок

| Проверка | Факт из лога | Итог |
|---|---|---|
| Признак ревизии 2 | попытка `1` **без** `mp=` (параметры по умолчанию, `Ls=100` намеренно вне окна) → `rc=-6`, а не `-2` | ✅ |
| T1: `mp=9,1000` (Rs < min) | `@MP:OK:Rs=9:Ls=1000:…` → `rc=-6` | ✅ |
| T2: `mp=100001,1000` (Rs > max) | `@MP:OK:Rs=100001:…` → `rc=-6` | ✅ |
| T3: `mp=13000,499` (Ls < min) | `@MP:OK:…:Ls=499:…` → `rc=-6` | ✅ |
| T4: `mp=13000,500001` (Ls > max) | `@MP:OK:…:Ls=500001:…` → `rc=-6` | ✅ |
| C0: `mp=10,500` (обе границы `==min`) | `@MP:OK:Rs=10:Ls=500:…` → `rc=-2` (карта) — гейт пропустил значения | ✅ |
| Значения доходят до гейта | `@MP:OK` печатает те же «плохие» значения, что и приняты в runtime | ✅ |
| PWM закрыт | 6× `@PWM:CR1=224:CCER=0:BDTR=7360:CNT=0`; `BDTR=0x1CC0` → MOE(bit15)=0, `CCER=0` | ✅ |
| FOC не запускался | `FOC started` — 0 вхождений; `RUN=1` — 0; `FAULT=1` — 0 (410 строк телеметрии) | ✅ |
| Коды отказа | всего 6 строк `@FOC:START`: 5× `-6`, 1× `-2`; кодов `0/-1/-3/-4/-5` нет | ✅ |
| Преамбула | `0` → `FOC stopped`; `f` → `@FAULT:CLEAR:STATUS=1`; `p?` → `CCER=0`, MOE=0; `a` → `@ADC:I1=2043:I2=2070:Ires=0:VBUS=7` | ✅ |
| HV | DC-link разряжен: `VBUS` raw 7 (≈0.66 В); energize-событий нет | ✅ |
| Диапазон входа | `em_stop1=1:em_stop2=1` во всех 410 строках телеметрии | ✅ |

## Выдержка сырого UART (только команды и ответы, без idle-спама)

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

## Матрица границ

| Ячейка | Параметры | Стенд |
|---|---|---|
| Rs < min | `9` | ✅ `-6` |
| Rs > max | `100001` | ✅ `-6` |
| Ls < min | `499` | ✅ `-6` |
| Ls > max | `500001` | ✅ `-6` |
| Rs == min ∧ Ls == min | `10, 500` | ✅ не `-6` (`-2`) |
| Rs == max ∧ Ls == max | `100000, 500000` | ⏳ **не проверено на плате** (на хосте покрыто `tests/foc_start_gate_test.c`) — блок §3a протокола, 4 команды, прошивка не нужна |

## Ограничения (что эта сессия НЕ доказывает)

1. Верхняя включённая граница `Rs=100000, Ls=500000` на железе не проверена (см. матрицу).
2. Калибровка оффсетов (`c`) в этой сессии не выполнялась, поэтому абсолютные значения токов в
   `@FOC`/`@ADC` фантомные (некалиброванные оффсеты); для этой задачи они и не нужны — гейт
   проверяет значения параметров, а не токи.
3. Осциллограмм PWM/апертур нет: критерий «PWM закрыт» доказан регистрами (`CCER=0`, `MOE=0`),
   а не измерением.
4. Сессия не покрывает `-3` (калибровка), `-4` (ADC arm), `-5` (PWM enable) — они недостижимы
   без загруженной карты.

## Замечания по ходу разбора

- `src/cli.c:174-177`: при любом ненулевом коде `PROTECT_RequestClear()` печатается один и тот
  же текст «fault NOT cleared: Vbus/current still out of range». В логе это видно прямо:
  `@FAULT:CLEAR:STATUS=1` (fault не был активен) и рядом эта фраза. Диагностический дефект (P2),
  safety-семантика не затронута; исправление — отдельным пакетом, вне вердикта TZ-01.
- В логе две строки, где эхо команды вклинилось в периодику (`…:FAULT=0>>> 1`, `…:em>>> 0`) —
  артефакт стримингового монитора; при автоматическом разборе нужна терпимость к этому.
- `BDTR=7360` (0x1CC0): `DTG[7:0]=0xC0` → dead-time 192 тика, совпадает с
  `deadtime_ticks=192` в identity-контракте.
- `@MP:OK` при `Ls=500001` показывает `Kp=13653360` — физически бессмысленные PI-коэффициенты;
  гейт как раз и не даёт их применить (дополнительный аргумент в пользу гейта).

## Ссылки

- `docs/ТЗ  interlock параметров двигателя в FOC_Start().md` — ТЗ-01 с §4 (инвариант дефолта),
  §9 (ревизия 2: контракт приоритета admission).
- `src/foc.c` — порядок гейтов `FOC_Start()`; `tests/foc_start_gate_test.c` — матрица A0/A/B/C/D/E.
