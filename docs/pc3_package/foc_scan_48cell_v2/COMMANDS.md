# Команды сессии (ред. 2)

Все команды выполняются из каталога пакета. Ничего, кроме перечисленного, на стенде
не отправляется (внутри окна — вообще ничего).

```bash
# шаг 0 (на стенде, до подачи питания): что на плате.
#   строку @PWM:FULL: печатает `pdump`, а НЕ `p?` (p? -> десятичные CR1/CCER/BDTR/CNT,
#   ни SYS, ни PSC, ни ARR он не печатает).
#   ожидание для ab1274d: SYS=170000000, T1:PSC=16, ARR=999, CR1=0x…E0 (CMS=11);
#   префикс группы только у первого поля (T1:PSC, T8:PSC): токенов T1:ARR/T1:CR1 нет.
#   тот же образ в p?: CR1=224:CCER=0:BDTR=7360:CNT=0
sysinfo ; a? ; pdump ; p? ; dump ; breakdiag

#   вердикт шага 0 (из софта, а не глазами): PASS/FAIL/ERROR + step0/result.json
#   чтение неблокирующее (timeout=0 + in_waiting): блокирующий read() с timeout
#   на VCP STLink не возвращается вообще (дефект B15) — инструмент не висит.
py -3 TOOLS/check_step0.py --port COM4 --out .\step0
py -3 TOOLS/check_step0.py --log step0_raw.log --out .\step0

# 0) самопроверка пакета: состав + SHA256SUMS + шкала + инварианты драйвера
py -3 TOOLS/check_package.py

# 1) шкала метрики (обязательно до клеток).
#    --clk = CLK из sysinfo (SystemCoreClock), НЕ TCLK: TCLK = CLK/(PSC+1),
#    подстановка TCLK = ошибка шкалы ред.1 (в 17 раз на этой плате).
#    --tclk — сверка, --pwm-expected (5000) — сверка с константой прошивки.
py -3 TOOLS/calc_expected.py --clk 170000000 --psc 16 --arr 999 --cms 3 --rcr 1 --tclk 10000000 --window 2.0
#    ожидание: f_pwm=5000.000, f_JEOS=5000.000, N_expected=10000, RESULT: PASS

# 2) план блока амплитуд, ничего не отправляя (печатает и "f" among them: 0)
py -3 TOOLS/scan_48cell.py --port COM4 --amp 2000,3000,4000,5000 --angles-el 0 --window 2.0 --n-expected 10000 --out .\run1 --dry-run

# 3) блок амплитуд (вал фиксирован, ANG=0) — 4 ячейки
py -3 TOOLS/scan_48cell.py --port COM4 --amp 2000,3000,4000,5000 --angles-el 0 --window 2.0 --n-expected 10000 --out .\run1

# 4) ось угла: остальные 11 положений × те же 4 амплитуды (44 ячейки; вместе с run1 — 48)
py -3 TOOLS/scan_48cell.py --port COM4 --amp 2000,3000,4000,5000 --angles-el 10,20,30,40,50,60,70,80,90,100,110 --window 2.0 --n-expected 10000 --out .\run2
```

## Гейт старта: коды ответа на `1` (исчерпывающе)

Ответ на `1` — не только `FOC started`. Прошивка проверяет гейты по порядку и печатает
`@FOC:START:FAIL:rc=<код>` (дефекты B16/B17: ред. 2 знала только `rc=-5`):

| ответ | код | что это значит | что делать |
|---|---|---|---|
| `FOC started` | 0 | energize-сборка, гейты пройдены | записать ответ, переходить к `i=` |
| `…rc=-2` (`map_unverified`) | -2 | нет карты реконструкции; в `@FOC` видно `map_id=M0:map_crc32=00000000` | скан не начинать: плата не подготовлена (карту получают захватом на месте — `mcarm=`/`mapcap run`/`mapcap drain`/`mapcap build=<profile_id>`), это вне рамок пакета |
| `…rc=-5` (`pwm_enable`) | -5 | на плате не energize-сборка (нужна собранная с `-DOEW_HS1_COMMISSIONING_RELEASE=1`) | скан не начинать |
| `…rc=-6` (`params_out_of_range`) | -6 | параметры двигателя (`Rs/Ls`) вне «здорового» окна: дефолт fail-closed | скан не начинать: нужны `mp=<Rs_mOhm>,<Ls_uH>` + `mpapply` или автотюн (вне рамок пакета) |

Дословный ответ записывается в возврат **всегда** (первая ячейка), а `rc != 0` = стоп серии.
Скрипт делает это сам (автостоп «FOC did not start»), но при разборе возврата код —
главный факт: он отделяет «стенд/сборка» от «плата не подготовлена к FOC».

Команды, которые в образе `ab1274d` **есть**, но в набор шагов пакета не входят
(нужны только чтобы понять, почему `rc != 0`, и чтобы подготовить плату; ничего из этого
стенд не обязан отправлять):

```text
mp=<Rs_mOhm>,<Ls_uH>       # задать параметры двигателя (окно гейта: Rs 10…100000, Ls 500…500000)
mpapply                    # применить их; без mp= отвечает @MPAPPLY:ERROR:-2
mapcap identity            # паспорт измерительной сессии: @MAP:IDENTITY:board=…:pwm=5000:arr=999:…
mapcap status              # состояние захвата: @MC:STATUS:state=…:term=…:avail=…:detail=…
mcarm=<profile_id>         # вооружить захват профилем: @MC:ARM:cap=…:rc=…:offsets_valid=…
mapcap run                 # burst из 8 импульсов — ЭНЕРГИРОВАННЫЙ шаг
mapcap drain               # 8× @MC:REC… и @MC:DRAIN:records=8
mapcap build=<profile_id>  # собрать И загрузить карту (нужен ЗАВЕРШЁННЫЙ захват)
mapload <994 hex>          # загрузить внешний артефакт карты (ровно 994 hex-символа)
```

Про `mcarm=`/`mapcap build=`:

* второй аргумент — **не** произвольное `<N>`: у платы фиксированный allow-list из 12
  профилей, `profile_id = 0x424F4152 + sector*2 + window` (десятичные `1112490322…
  1112490333`; источник — `src/map_capture_profiles.c`, комментарий «BOAR»). Чужой id даёт
  `@MC:ARM:BLOCKED:PROFILE` / `@MAP:BUILD:BLOCKED:PROFILE`;
* `mapcap build=` — это шаг **построения артефакта**, а не измерение: при незавершённом
  захвате он отвечает `@MAP:BUILD:BLOCKED:CAPTURE_STATE=…:TERM=…:AVAILABLE=…`, при
  расхождении identity — `@MAP:BUILD:BLOCKED:IDENTITY`. Порядок точки (`mcarm=` → `mapcap
  run` → `mapcap drain` → `mapcap status` → `mapcap build=`) зафиксирован в
  `docs/ACCEPTANCE_LESSONS.md` §25.10 и в runbook `README_BOAR_CAPTURE_PC3.md` §3.

Про `mapload` (дефект B18 — вывод «команды в образе нет» был получен зондом неверной
формы, а не отказом платы):

* команда в образе **есть**: зонд `mapload 00` (заведомо неверная длина) отвечает
  `@MAP:LOAD:FAIL:DECODE` — разбор не начинается, карта не грузится, состояние не меняется
  (сырой лог: `docs/evidence/incident_20261004_hwbreak/09_capability_probe_raw.log`);
* формат — `mapload ` + ровно **994** hex-символа (wire 497 байт), иначе `@MAP:LOAD:FAIL:DECODE`;
* слово `mapload` без аргумента отвечает `unknown` — это НЕ признак отсутствия команды:
  парсер образа распознаёт только строку с аргументом (`strncmp(line,"mapload ",8)`);
* даже корректная длина не гарантирует загрузку: артефакт проходит admission и при
  расхождении identity отвечает `@MAP:LOAD:FAIL:COMMISSION`. Артефакты от 08.09
  (`мотор анализ\map_upload_pkg_20260908\*.bin`) несут identity `pwm=294` — это дефект D3
  (двойной учёт `PSC`), такие identity «отвергаются как устаревшие»
  (`src/map_capture_profiles.c:191-199`; живая плата даёт `pwm=5000`). Поэтому на этой плате
  путь к карте — захват на месте (`mcarm=`/`mapcap run`/`mapcap drain`/`mapcap build=`), а не
  внешний артефакт.

## Что отправляется в одной ячейке

```text
sysinfo -> 1 -> i=<Id_ma>,<Iq_ma> -> <окно: только чтение> -> 0 -> sysinfo
пост-мортем: dumpa, dump, p?, sysinfo, a?, breakdiag
```

`i=` считается из амплитуды и угла: `Id = A·cos(θ)`, `Iq = A·sin(θ)` (мА). Порядок
«сначала `1`, потом `i=`» обязателен: первый `FOC_Start()` вызывает `FOC_Init()`, который
ставит `Id_ref = 2000 мА`; уставка из `i=` переживает повторные старты, но должна быть
отправлена после этого момента. Принятое значение видно в окне как `Id_ref=`/`Iq_ref=` в `@FOC`.

## Флаги, меняющие смысл данных (перечислить в возврате, если использованы)

* `--no-set-current` — `i=` не отправляется, `amp/angle` становятся только метками;
* `--allow-nonstart` — только отладка: не останавливаться, если ответ на `1` не `FOC started`;
* `--no-sd-stop` — стендовое исключение: не останавливаться на низком уровне SD;
* `--no-sd-stop` без записи в возврате = нарушение протокола §5;
* `--eol lf` — если терминал платы ждёт `\n` (по умолчанию `crlf`);
* `--postmortem` — список пост-мортем-команд (по умолчанию `dumpa,dump,p?,sysinfo,a?,breakdiag`).

`--angles-el` — число попадает в `cells.csv` как метка угла: если вал не крутили, ставьте одно
значение и описывайте положение в возврате. Амплитуды — в мА, снизу вверх, не выше 10000
(прошивка клампит `i=` по `FOC_I_MAX_MA`).

## Сборка и проверка ZIP (для отправителя пакета)

```bash
py -3 scripts/build_foc_scan_48cell_pkg.py            # manifest + check + ZIP в build/ + .sha256
py -3 scripts/build_foc_scan_48cell_pkg.py --normalize --manifest   # если check ругается CRLF FOUND
py -3 scripts/build_foc_scan_48cell_pkg.py --verify build/FOC_SCAN_48CELL_PC3_V2_<дата>.zip
```

ZIP детерминирован (одинаковый вход → одинаковый SHA-256), сайдкар `<zip>.sha256` отправляется
вместе с ZIP: у ред. 1 сайдкара не было — получатель не мог проверить целостность.
