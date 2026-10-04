# Стенд ПК-3, 04.10.2026: комиссионинг выполнен, но образ на плате не может арм-ить BOARD-профиль (дефект §25.12 подтверждён живым прогоном)

**Итог одной строкой.** RAM-параметры двигателя и нули ADC на плате выставлены и подтверждены
живыми ответами. Арм профиля BOARD невозможен: на плате лежит образ «commissioning»-артефакта CI
(`docs/pc3_package/pc3_al_pkg_v2`), собранный с `-DOEW_MAP_SYNTHETIC_PROFILE=1 -DOEW_HOST_TEST=1` —
на таком образе ветка BOAR вырезана (`@MC:ARM:BLOCKED:PROFILE`), а синтетическая ветка (`SYNT`)
не проходит проверку identity при `mapcap build`. 48-ячеечный скан (`foc_scan_48cell_v2`) на этом
образе запустить нельзя; энергия для скана не требовалась и не подавалась (`"f" sent: 0`, VBUS=0).

Опасных операций не было: `mapload` не выполнялся ни для одного артефакта, демонстрационные
артефакты карт (§4) к загрузке не пригодны. Плата после сессии безопасна: `state=0`, `FAULT=0`,
CCER/MOE выключены (проверено `p?`/`@FOC`).

## 1. Выполнено на стенде (дословные ответы платы)

| # | шаг | команда | ответ платы (verbatim) | лог |
|---|-----|---------|------------------------|-----|
| 0 | предполётный шаг 0 | `py -3 TOOLS/check_step0.py --port COM4` | `verdict : PASS (rc=0)` — CLK 170000000, PSC 16, ARR 999, `pdump SYS == sysinfo CLK`, `RCR: NOT MEASURED` (честно, командой этого образа RCR не читается) | `backup/bench_20261004/step0/` |
| 1 | RAM-параметры | `mp=15120,19410` | `@MP:OK:Rs=15120:Ls=19410:Rr=0:Lm=0:Tr=0:Ke=0:p=0:J=0:Kp=530022:Ki=82575:Lsig=19410:AP=1` | `commission/02_params.log` |
| 2 | применение | `mpapply` | `@MPAPPLY:OK:Rs=15120:Ls=19410:Rr=0:Lm=0:Tr=0:Ke=0:p=3:J=0:Kp=530022:Ki=82575:Lsig=19410:AP=1` | `commission/02_params.log` |
| 3 | гейт старта | `1` | `FOC start blocked: current map unverified …` + `@FOC:START:FAIL:rc=-2 (… -2=map_unverified … -6=params_out_of_range)` — гейт −6 (`params_out_of_range`) пройден, следующий блокер — пустая карта (`map_crc32=00000000`) | `commission/02_params.log` |
| 4 | нули ADC | `c` | `@ADC:CAL:offset_i1=2040:offset_i2=2069:offset_ires=0`; следом `@ADC:STATUS:offset_i1=2040:stream=0` | `commission/03_calib.log` |
| 5 | контроль identity | `mapcap identity` | `@MAP:IDENTITY:board=7:pwm=5000:arr=999:trig=0x4F455731:off=0:dt=192:adc_clk=42500000:sample_x2=1281:res=0:acs=0x26B9B97B:ccs=0x13552B12` (`ccs` совпал с декларацией пакета; до калибровки был `0xABE94C77`) | `commission/03_calib.log` |
| 6 | арм BOARD | `mcarm=1112490322` (`BOAR`) | `@MC:ARM:BLOCKED:PROFILE` | `commission/04_arm.log` |
| 7 | арм SYNTHETIC | `mcarm=1398361684` (`SYNT 0x53594E54`) | `@MC:ARM:cap=3:rc=0:offsets_valid=1:inj_start_rc=0` (state=ARMED); затем `mapcap abort` → `@MC:ABORT:rc=-14` (не активно) | `commission/07_arm_synt_correct.log`, `05_abort.log` |
| 8 | скан 48 ячеек (как поставлен) | `py -3 TOOLS/scan_48cell.py --port COM4` | останов на 1-й ячейке: `FOC start blocked: motor Rs/Ls out of sane range (run autotune, then mp=/mpapply)` + `@FOC:START:FAIL:rc=-6`; `cells: 1 of 4`, `"f" sent: 0`, `SESSION ABORTED` | `run1/`, `scan_run1.log` |

По шагу 8: скан был запущен ДО комиссионинга, с дефолтными `Rs/Ls` → плата отбила старт гейтом −6,
скан корректно остановился, энергию не включал. Повторный запуск скана требует арм-а карты (§5).
По шагу 3: цепочка гейтов fail-closed работает как заявлено — `rc=-6` (диапазон параметров) →
`rc=-2` (карта не подтверждена) → PWM не включается.

## 2. Доказательство: на плате лежит образ из пакета `pc3_al_pkg_v2` (а не из пакета скана)

* Дампы Flash (`backup/flash_2026100*.bin`, 5 снимков 02–04.10) побайтово идентичны:
  sha256 `2cbab38a9182a48e1537bd5dfeb16888772514cdf987c37cc6a5fb93659329e2`
  (первые 90828 Б — образ, хвост 433460 Б — `0xFF`).
* `py -3 tools/check_firmware_image.py --dump backup/flash_20261004_221901.bin --artifact-zip docs/pc3_package/pc3_al_pkg_v2/firmware/ci733/artifact_ci733.zip`
  → `образ в дампе (первые 90828 Б): СОВПАДАЕТ (sha256 351dbbb1aeadb900…c3b63158)`,
  `хвост дампа 433460 Б: стирание 0xFF`.
* Тот же sha256 (`351dbbb1…`) заявлен в `docs/pc3_package/pc3_al_pkg_v2/manifest.json`
  (`identities.al.binary_sha256`, `source_commit 4507983`).
* Манифест того же пакета фиксирует дефайны сборки: `OEW_MAP_CAPTURE=1`, `OEW_MAP_L3=1`,
  `PWM_OEW_BOARD_REVISION=7`, **`OEW_MAP_SYNTHETIC_PROFILE=1`**, **`OEW_HOST_TEST=1`**,
  `OEW_HS1_COMMISSIONING_RELEASE=1`.

## 3. Корневая причина (подтверждение §25.12/§25.13 живым прогоном)

* `SYNT`-профиль вкомпилирован → `mcarm 1398361684` проходит (`rc=0`).
* `BOAR`-профиль вырезан препроцессором → `mcarm 1112490322` → `@MC:ARM:BLOCKED:PROFILE`.
* Статическая проверка образа (`tools/check_firmware_image.py --image …`) даёт класс **SYNTHETIC**:
  `SYNT acs 0x13572468`, `SYNT ccs 0x24681357`, `SYNT adc_clk 170000000` — есть;
  `BRD acs 0x26B9B97B`, `BRD ccs 0x13552B12`, триггер `0x4F455731` — отсутствуют.
* Кандидатный кампанийный образ (собран без SYNT/HOST_TEST, §6) даёт класс **BOARD** —
  т.е. различие классов образов проверяется статикой, без железа.

## 4. Почему на этом образе карту получить нельзя (даже с энергией)

Даже если бы BOARD-профиль был доступен, на текущем образе путь к карте закрыт по цепочке:

* `mcarm` для `SYNT` проходит (`rc=0`), но `mapcap build=<id>` строит карту из синтетических
  записей, а `MapBuilder_AddRecord` отвергает записи, где `tim1_ccr[i] != tim8_ccr[i]`
  (Уровень L3 требует совпадения CCR обоих таймеров), поэтому `mapcap build` для BOAR-офсетов
  упирается в identity-проверку, а не в физику. Обходной путь (для честной кампании):
  `mapcap drain` → сборка записи на хосте → `mapload <994 hex>`.
* Артефакты карт в `мотор анализ/map_upload_pkg_20260908/` **к загрузке не пригодны**:
  во всех четырёх recon-строки — единичная матрица (`m00=1000,m01=0,m10=0,m11=1000`),
  а `oew_map_v2_stable_sig.bin` отличается от прочих только заглушкой `pwm=294`
  (живая плата печатает `pwm=5000`) ⇒ это демонстрационные файлы, не измерения.
  `mapload` любого из них дал бы «карту», не связанную с этим железом — при живом токе это
  прямое нарушение §25.12/§25.13. **Не выполнялось.**

## 5. Что НЕ сделано (и почему)

| Не сделано | Причина |
|---|---|
| Арм BOARD-профиля, `mapcap run/drain`, сборка карты | Профиль вырезан в текущем образе (`BLOCKED:PROFILE`) |
| `mapload` любых артефактов | Все доступные артефакты — демонстрационные (§4) |
| Подача DC-link (24–36 В) | Скан останавливается раньше включения PWM; энергии не требовалось |
| Перепрошивка платы | Изменяющее состояние действие: решение владельца (§7), образ-кандидат готов (§6) |

## 6. Готовое решение: кампанийный образ класса BOARD (собран и проверен офлайн)

Собрано и проверено без железа:

```
$ py -3 scripts/build_campaign_image.py --log backup/campaign_build.log
HEAD=4ada5b49865841367e57290fd54ca089df383a9e
DEFINES=['OEW_MAP_CAPTURE=1', 'OEW_MAP_L3=1', 'PWM_OEW_BOARD_REVISION=7', 'OEW_HS1_COMMISSIONING_RELEASE=1']
image=build/firmware.bin size=83744 sha256=6bb2a31459050d9e9442ab5205dd27988bde99fda916d6c6b30840a5b85a76ce
VERDICT=BOARD — BOARD-ветка вкомпилирована => BOAR-профиль доступен (подтвердить `mcarm <BOAR id>` -> rc=0)
RESULT: PASS
```

* набор дефайнов совпадает с канонической командой сборки стенда
  (`tools/boar_energize_ready.py:150-157`), при этом `SYNT/HOST_TEST` НЕ добавляются;
* сборка детерминирована: жёсткая очистка `shutil.rmtree` + 37 пересобранных объектов дали
  тот же sha256 `6bb2a314…`, что и первая сборка в этой сессии;
* provenance рядом с образом: `build/firmware.provenance.json`
  (commit / дефайны / sha256 / класс образа);
* скрипт fail-closed: с `-DOEW_MAP_SYNTHETIC_PROFILE=1` или `-DOEW_HOST_TEST=1` он откажет
  (код 2) ДО запуска `make` — «кампанийный образ из SYNTHETIC-набора» собрать нельзя.

Порядок действий при одобрении владельца (по шагам, с откатом):

```powershell
make backup                                     # дамп Flash (только чтение) + SHA-256  <-- ДО перепрошивки
py -3 scripts/build_campaign_image.py           # fail-closed: ожидаем VERDICT=BOARD, rc=0
py -3 tools/check_firmware_image.py --image build/firmware.bin     # rc=0
make flash                                      # STM32_Programmer_CLI -c port=SWD mode=UR -w build/firmware.bin 0x08000000 -v -rst
make backup ; py -3 tools/check_firmware_image.py --dump <свежий дамп> --image build/firmware.bin
py -3 tools/pc3_commission.py params ; py -3 tools/pc3_commission.py calib
py -3 tools/pc3_commission.py arm 1112490322    # ожидаем @MC:ARM:cap=…:rc=0  (BOARD-профиль)
```

Откат: `backup/flash_2026100*.bin` (образ + хвост `0xFF`) либо повторная прошивка пакетного
`firmware.bin` из `docs/pc3_package/pc3_al_pkg_v2/firmware/ci733/artifact_ci733.zip`
(sha256 `351dbbb1…`) — оба варианта кладут плату в текущее (безопасное) состояние.

Замечание по `tools/boar_energize_ready.py`: он собирает образ с теми же дефайнами, но требует
подписанный G0-approval для TEST3; для кампании 48-ячеечного скана достаточно
`scripts/build_campaign_image.py` — он печатает provenance и класс образа.

## 7. Что нужно от владельца

1. **Решение о перепрошивке** платы кампанийным образом `6bb2a314…` (§6) — иначе комиссионинг
   ПК-3 на этом стенде упирается в дефект пакета §25.12 и 48-ячеечный скан невозможен.
2. Альтернатива, если перепрошивать нельзя: перевыпустить пакет `pc3_al_pkg_v2`
   (`build_variant = firmware-commissioning` БЕЗ `OEW_MAP_SYNTHETIC_PROFILE`/`OEW_HOST_TEST`)
   и получить его CI-артефакт — тогда стенд перепрошивается «пакетным» образом.
3. **DC-link 24–36 В** понадобится позже — только после успешного `mcarm BOAR` (§6); 60 В
   по-прежнему вне допуска для этой задачи.
4. Подтверждение, что цель скана = `foc_scan_48cell_v2` (он ожидает образ кодовой линии
   `ab1274d`, а на плате `4507983`; идентичность проверена живыми регистрами — расхождений нет,
   но линии разные: merge-base `11ad97d`).

## 8. Инструменты и артефакты сессии

| Файл | Назначение |
|---|---|
| `tools/check_firmware_image.py` | класс образа (SYNTHETIC/BOARD), побайтовое доказательство «дамп == артефакт пакета»; коды 0/1/2 |
| `tools/pc3_commission.py` | шаги комиссионинга по VCP (status/params/calib/arm/capture/abort) с гейтами |
| `tests/test_check_firmware_image.py` | pytest: 8 тестов (класс образа, дамп↔ZIP, коды выхода, fail-closed дефайны) — PASS |
| `scripts/build_campaign_image.py` | сборка кампанийного (BOARD) образа + provenance + fail-closed проверка класса |
| `backup/bench_20261004/` | сырые логи сессии: `step0/`, `commission/01..07`, `run1/` (скан), `scan_run1.log` |
| `backup/flash_2026100*.bin` | дампы Flash (откат); sha256 совпадают во всех снимках |

Примечание: каталог `backup/` вне git-индекса (`.gitignore:83`) — дампы Flash и сырые логи
сессии остаются локальными носителями для отката; в коммит попадают отчёт, инструменты и тест.

Проверки инструментов на этом ПК:

```
py -3 -m pytest tests/test_check_firmware_image.py -q           -> 8 passed
py -3 tools/check_firmware_image.py --dump backup/flash_20261004_221901.bin \
        --artifact-zip docs/pc3_package/pc3_al_pkg_v2/firmware/ci733/artifact_ci733.zip
        -> образ в дампе: СОВПАДАЕТ (sha256 351dbbb1…); ВЕРДИКТ: SYNTHETIC; rc=1
py -3 tools/check_firmware_image.py --image build/firmware.bin   -> ВЕРДИКТ: BOARD; rc=0
```

## 9. Приложение: побочная находка полного `pytest` на этом ПК (не дефект §25.12)

Полный прогон `py -3 -m pytest tests -q` на ПК-3 (Windows, консоль cp1251) дал
`4 failed, 659 passed, 2 skipped` — все 4 падения в HWT-наборе:
`tests/test_hwt_backup.py::test_verify_ok_and_mismatch`,
`tests/test_hwt_pipeline.py::test_list_and_preflight_pass`,
`tests/test_hwt_pipeline.py::test_preflight_fails_on_missing_contract`,
`tests/test_hwt_pipeline.py::test_simulated_run_passes_and_is_marked`.

Причина — кодировка вывода подпроцессов, а не логика: тесты ищут русские строки
(`РАСХОЖДЕНИЕ`, `CRC образа`, `контракт не выполнен`, `СИМУЛЯЦИЯ`) в тексте, декодированном
как cp1251 (в логе это видно как `Р РђРЎРҐРћР–Р”Р•РќРР•`). Проверка не связана с этим
коммитом (файлы HWT не трогались):

```
$ py -3 -m pytest tests/test_hwt_backup.py tests/test_hwt_pipeline.py -q     -> 4 failed
$ $env:PYTHONUTF8='1'; py -3 -m pytest tests/test_hwt_backup.py tests/test_hwt_pipeline.py -q -> 12 passed
```

Т.е. на Linux-CI (UTF-8 по умолчанию) набор зелёный; на Windows-хосте для зелёного прогона
нужен `PYTHONUTF8=1` либо явный `encoding=` при чтении вывода в самих тестах HWT.
Правка тестов HWT в это ТЗ не входит (один пакет = одно ТЗ) — зафиксировано здесь.
Новый тест этого коммита не затронут: `tests/test_check_firmware_image.py` — 8 passed.
