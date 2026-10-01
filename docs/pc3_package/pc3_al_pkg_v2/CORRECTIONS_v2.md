# PC-3 al-пакет: правки docs ред. 1 → ред. 2

Дата: 01.10.2026. Основание: фактическое дерево `4507983`
(`0fba575` — отдельное docs-only изменение поверх него).

Статус пакета не меняется: **физическая квалификация `al` = NOT_PASSED**.
Правки касаются только документации; прошивка и её хеш не затронуты.

---

## 1. Удалено

1. **Правило «`i=` не использовать / ответ unknown → уставка через `s=`»**
   (`docs/AL_PROCEDURE.md` ред. 1, раздел 6) — опровергнуто кодом `4507983`.
   Причина ошибки: утверждение выведено из справочного среза, а не из
   фактического дерева.
2. **Раздел «Команды, отсутствующие в справочном срезе»**
   (`docs/COMMANDS.md` ред. 1), где `i=<id,iq>` и `cv` числились
   отсутствующими, — заменён разделом «Наличие команд: проверено grep'ом».

## 2. Команды: опровергнуто кодом (evidence @ 4507983)

| Утверждение ред. 1 | Факт | Доказательство |
|---|---|---|
| команды `i=` нет в образе | **есть**, штатная | `src/cli.c:427` `sscanf(line,"i=%d,%d") == 2` |
| — | ack `@I:OK:Id=…:Iq=…` — эхо ЗАПРОШЕННЫХ значений | `src/cli.c:427` |
| — | идут в `FOC_SetIdRef/FOC_SetIqRef`, clamp ±10000 мА | `main.c:519` → `src/foc.c:364-368`, `FOC_I_MAX_MA` `foc.c:260` |
| команды `cv` нет | **есть**: offset VBUS | `src/cli.c:75-88`, `@ADC:CV:OK:offset_vbus=…` |
| `mp=` — только Rs,Ls | парсер 8 полей, min 2, поля >0 применяются | `src/cli.c:398-416` |
| — | `Ls<=0`/`Rs<=0` → отказ `-2` | `FOC_SetMotorParams` `src/foc.c:463-465`, маппинг `.foc_set_params` `main.c:778` |
| — | успех/отказ: `@MP:OK:…:AP=1` / `@MP:ERROR:<rc>` | `src/cli.c:415-416` |
| Route B «известен по ack» | подтверждено дословно | `@AT:RS_IV:OK:Rs=%ld` `autotune.c:877`; `@AT:OEW:OK:Ls=…:Isat=…` `autotune.c:1637`; `@PI:APPLIED:Kp=…:Ki=…:AP=1` `cli.c:424` |
| коды отказа `1` | 0/−1…−6, легенда печатается самим ответом | `src/foc.h:44-52`; `src/cli.c:252-253` |
| `PROTECT_CheckFrame()` по сырым токам DC-link | по `idc1_ma/idc2_ma/vbus_mv` кадра | `src/protect.c:96-112` |
| PROTECT слеп вне FOC | подтверждено: вызов только при `foc_running() && timer_enabled()` | `src/adc_dispatch.c:24-28` |
| поле `TH` в телеметрии | есть | `src/telemetry_format.h:26` |
| `@AL`-поля u/uu/v/vv/w/ww | подтверждено | `src/cli.c:175` |
| кадр ADC обновляется при `FOC_IsRunning()==0` | подтверждено: публикация в `ADC_InjectedIrq` → `adc_commit_injected_frame` → `adc_publish`, независимо от FOC; гейт в `adc_dispatch.c` касается только PROTECT и `FOC_RunFrame` | `src/adc.c:392-416` |

## 3. `docs/SAFETY.md`: ошибки ред. 1, найденные при сверке чисел

| Утверждение ред. 1 | Факт @ 4507983 | Доказательство |
|---|---|---|
| «`ch`: собственного `AT_SafetyCheck` нет» | **неверно**: `ch` вызывает `AT_SafetyCheck` до возбуждения мостов | `src/autotune.c:336`, тело — `autotune.c:596-665` |
| «остаточные ≤ 20 мА с перекалибровкой offset» | порог **50 мА** по `|I1|,|I2|,|Ires|`, до 5 попыток по 100 мс, затем `@AT:WARN:RESIDUAL_CURRENT` и отказ | `src/autotune.c:642-660` |
| «`ch`: один импульс 5 % на 300 мкс» | импульс `PWM_SetDuty1(5,0,0)` + `PWM_SetDuty2(100,…)`, ожидание **адаптивное ≤ 20 мс** (40 × 500 мкс), выход при max|ΔI| ≥ 50 мА или |I| > 2000 мА; 300 мкс — текст комментария, а не окно | `src/autotune.c:396-409` |
| «программный порог 8000 мА» без указания, чей | 8000 мА — `AUTOTUNE_MAX_CURRENT_MA` (`autotune.h:7`); у защиты FOC-окна — **12000 мА** (`PROTECT_I_MAX_MA`), VBUS 8000…350000 мВ | `src/autotune.h:7`, `src/protect.c:6-8` |
| «ступени ≤ 15 % / рампа ≤ 50 %» | `iv`: ступени 2/4/6/8/10/12/15 %; `oew`: рампа 5…50 % шагом 1 % | `src/autotune.c:765`, `src/autotune.c:1457-1462` |
| «порог ≥12 В — маршрут B; `al` может переиспользовать `AT_SafetyCheck`» | `al` НЕ вызывает `AT_SafetyCheck`; admission `PWM_AlignApertureStart` = arr≠0, PWM off, injected не взведён, `!PROTECT_IsFault`, `!g_clock_fail`, `PWM_HardwareInterlockHealthy()` — Vbus не проверяется | `src/cli.c:140-190`, `src/pwm.c` (`PWM_AlignApertureStart`) |
| порог Vbus 12000 мВ маршрута B | в `AT_SafetyCheck` для ch/iv/oew; `oew` проверяет ещё раз сам (rc −7) | `src/autotune.c:621`, `src/autotune.c:1446-1448` |
| 1 LSB VBUS ≈ 100.73 мВ | 125 · 3300 / 4095 = **100.733 мВ** | `src/adc.h:8,11,20`, `src/adc.c:89-97` |
| «`pi=N` откажет при Ls ≤ 0 или Rs ≤ 0» | точнее: гейт `AT_SaneLs/AT_SaneRs` (Ls 500…500000 мкГн, Rs 10…100000 мОм) + полоса 100…500 Гц | `src/autotune.c:2188-2210`, `src/autotune_math.h:11-14`, `src/autotune.c:150-151` |
| «Autotune_Init обнуляет pole_pairs» | `memset` всей `g_motor_params` | `src/autotune.c:2319-2321` |

## 4. Уточнения, добавленные в ред. 2

- `@AL:FAIL:start (PWM off, no fault, SD high, ADC idle)` — дословный
  admission-отказ `al` (`src/cli.c:169`).
- Во время удержания `al` ADC injected **взведён** (`PWM_AlignApertureStart`
  вызывает `ADC_InjectedStart`), то есть JADSTART = 1 — штатное состояние
  удержания, а не признак аномалии; ветка наблюдения — `AL_PROCEDURE` §3.
- `c` печатает `offset_i2` и `offset_ires` напрямую (`src/cli.c:71`), `cv` —
  `offset_vbus` (`src/cli.c:86`); выводить offset I2 из нулевого raw нужно
  только если `c` отказал (`@ADC:CAL:FAIL:rc=…`).
- **Ловушка `i=`**: `@I:OK` эхо запрошенных значений, а не результата клампа —
  `i=20000,0` ответит `Id=20000`, фактическая уставка 10000.

## 5. Что в репозитории править НЕ нужно (проверено)

Ложного правила в репозитории нет — оно существовало только в docs пакета:
- `TZ_CLI_MAIN_LOOP_EXTRACTION.md` (HEAD и `origin/main`): `i=` в реестре команд;
- `origin/ai2/shunt-cal-campaign-v1 : docs/TZ_ALIGN_CHECK_AND_TWO_GATE_FIRST_START_PC3.md:195`:
  «`i=<Id>,<Iq>` (пара обязательна)», введено коммитом `0fba575`.

## 6. Не перепроверялось в этом проходе

- `ci_run: 733` / `ci_status: success` в `manifest.json` — перенесено из ред. 1;
  локального доступа к прогону CI в этом проходе не было.
- `frozen_scan` (`ab1274d`, `d2bc0439...`, 88 020 Б) — перенесено из ред. 1;
  этим пакетом не трогается.
- Коэффициент 63 мВ/А тракта I1/I2 — измеренная ранее величина; арифметика
  `3300 мВ / 4096 / 63 мВ/А = 12.788 мА/отсч` сходится, сам коэффициент
  не переизмерялся.

## 7. Что нужно сделать в пакете

1. Заменить `docs/COMMANDS.md`, `docs/AL_PROCEDURE.md`, `docs/SAFETY.md`;
   обновить `README.md`, `docs/IDENTITIES.md`, `manifest.json`.
2. Пересобрать `SHA256SUMS` (хеши docs изменились) и архив.
   `manifest.json` в части `frozen_scan` и `al.binary_sha256` НЕ меняется:
   прошивка та же.
3. Физическая квалификация `al` по-прежнему `NOT_PASSED`; успешный CI не даёт
   оснований для выводов о готовности FOC.

---

## 8. Правки по приёмке ред. 2 (внесены в эти же файлы)

1. **Ветка наблюдения инвертирована** (`docs/AL_PROCEDURE.md` §3). Во время
   удержания штатно JADSTART = 1 и живая `@FOC` — это основной канал критериев
   1–3; JADSTART = 0 в удержании объявлен аномалией (стоп + пост-мортем), а не
   альтернативным способом измерения. Прежняя ветка «два `a` с Δraw · 12.79»
   удалена как способ наблюдения в удержании; сама формула сохранена для
   префлайта и пост-мортема (там JADSTART = 0).
   Доказательства: `PWM_AlignApertureStart()` взводит injected
   (`ADC_InjectedStart()`, `src/pwm.c`); при взведённом JADSTART
   `ADC_StartConversion()` сразу возвращает −1 (`src/adc.c`), а raw-геттеры
   читают последний опубликованный кадр (`src/adc.c:688-691`) → `a` в
   удержании даёт молча устаревшие значения. Вне путей `al`/Stop снятие
   injected не происходит: все вызовы `ADC_InjectedStop()` — `pwm.c:433,594,774`,
   `foc.c:634`, `autotune.c:74,349,505`, `map_capture.c:82`.
2. **Срединный `dumpa`** понижен до опционального кросс-чека (JDR1/JDR2 против
   значений `@FOC`); обязательным остаётся пост-мортем.
3. **Offset-семантика маршрута B** добавлена в `docs/SAFETY.md`:
   `AT_SafetyCheck` легально перекалибрует offset после спада тока
   (`src/autotune.c:642-648`), поэтому расхождение offset'ов до/после маршрута
   не является дрейфом; в логе помечать явно. Там же зафиксировано, что
   firmware-гейт принимает остаток до 50 мА после ожидания спада.
4. **Опечатка в тексте приёмки**: «адаптивное ≤ 20 мкс × 40» — правильно
   **500 мкс × 40 = 20 мс** (`src/autotune.c:396-409`); 20 мкс как окно
   дискретизации в коде не встречается.

---

## 9. Третий маршрут CLI и вариант сборки (добавлено после второй сверки)

Найдено при перепроверке собственного правила «нет в образе» по всему дереву,
а не только по `src/cli.c`; ошибка того же класса, что исходное ложное правило
про `i=`.

1. **CLI живёт не только в `cli.c`.** Нераспознанное уходит в
   `ops->mapcap_command` (`src/cli.c:278`) → `cli_mapcap_command`
   (`main.c:541-647`): `mcarm=<id>`, `mapcap run|drain|abort|status`,
   `mapcap identity` и `mapcap build=<N>` (оба — под `#if OEW_MAP_L3`),
   `mapload <994 hex>`. В production-сборке тело компилируется как
   `(void)line; return 0;` (`main.c:644-646`) → команды отвечают `unknown`.
2. **`eangle` — не команда**, а поле телеметрии `@VFLOG` (`main.c:253`,
   разбирает `telem_parser.py`). Реально отсутствующие команды после сверки
   по всему дереву: `vci=`, `jc`.
3. **Вариант сборки — часть процедуры, а не деталь.** CI собирает три образа
   (`.github/workflows/ci.yml` @4507983): `firmware` (`make`), `firmware-commissioning`
   (defines `OEW_MAP_CAPTURE=1 OEW_MAP_L3=1 PWM_OEW_BOARD_REVISION=7
   OEW_MAP_SYNTHETIC_PROFILE=1 OEW_HOST_TEST=1 OEW_HS1_COMMISSIONING_RELEASE=1`)
   и `firmware-bench-aperture` (+`OEW_BENCH_APERTURE=1`). На production-образе
   `al` не создаёт вектор (default-deny заглушка `PWM_AlignApertureStart`,
   `src/pwm.c`), а `mapcap`/`mapload` отсутствуют → гейт `-2 map_unverified`
   недостижим. Прошивать нужно `firmware-commissioning` от того же source commit.
4. **Synthetic-профиль скомпилирован в commissioning-образе**: активация
   требует ОДНОВРЕМЕННО `OEW_MAP_SYNTHETIC_PROFILE` и `OEW_HOST_TEST`
   (`src/map_capture_profiles.c:33-38`), и оба флага в наборе CI. Значит
   `mapcap build=` с профилем `0x53594E54` ("SYNT") может дать загруженную
   карту из синтетики — она физическим доказательством не является; для гейта
   `-2` используется `mapload` измеренного артефакта либо board-профиль
   "BOAR" `0x424F4152`.
5. Цикл `mapload`: ровно 994 hex-символа (`OEW_CURRENT_MAP_WIRE_SIZE * 2`),
   `MapArtifact_DecodeBinary` → `CurrentMap_LoadMeasured`; ответы
   `@MAP:LOAD:OK:crc=…:cid=…` / `@MAP:LOAD:FAIL:DECODE`.

Внесено в `docs/COMMANDS.md` (раздел команд commissioning-адаптера и новое
правило приёмки), `docs/SAFETY.md` (ложный симптом «al не даёт тока»),
`README.md` (раздел «Вариант сборки»), `manifest.json` (`build_variant` +
`build_variant_forbidden`).
