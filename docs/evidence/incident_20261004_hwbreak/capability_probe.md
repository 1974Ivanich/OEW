# Зонд словаря команд образа (2026-10-04, COM4) — дефект B18 пакета

**Зачем.** Раздел B17 пакета `foc_scan_48cell_v2` (ред. 2) утверждал как факт: «команды
`mapload` в образе НЕТ: внешний артефакт карты не грузится». Утверждение опиралось на ответ
`unknown` в `06_cmd_probe_raw.log`, куда ушло слово `mapload` **без аргумента**. Такой ответ
ожидаем независимо от сборки: парсер образа распознаёт только строку с нагрузкой
(`strncmp(line,"mapload ",8)` — `main.c`). Нужен был корректно поставленный зонд.

**Когда/чем.** 2026-10-04, плата ПК-3, `COM4` (`STLink Virtual COM Port`), 115200, образ
`ab1274d` (тот же, что в инциденте). Инструмент: `_bench_capability_probe.py`
(в репозитории; сырой лог — `09_capability_probe_raw.log`).

**Безопасность.** Перед зондом — fail-closed гейт по живой телеметрии: `@FOC frames=8 RUN=0
FAULT=0`; иначе прогон останавливается и на плату ничего не уходит. Отправлены только
читающие/синтаксические команды:

```text
p?  breakdiag  mapcap status  mapcap identity  mapload 00  mapcap build=
```

Не отправлялись: `1`, `0`, `i=`, `a`, `c`, `f`, `mp=`, `mpapply`, `mcarm=`, `mapcap run`,
`mapcap drain` — то есть ни одна команда, которая энергирует силовую часть или меняет
состояние. `mapload 00` и `mapcap build=` не меняют состояние **по устройству**: у первой
длина (2 символа) отсекается до разбора, у второй `sscanf` не находит число и обработчик
печатает `err: mapcap build=<profile>`.

## Результат (дословно)

| зонд | ответ платы |
|---|---|
| `p?` | `@PWM:CR1=224:CCER=0:BDTR=7360:CNT=0` (MOE=0, CCR1=500 → ШИМ выключен) |
| `breakdiag` | `@BRK:valid=1:seq=1:src=TIM1:cyc=0:sr=81,81:sd=1,1:bd=1CC0,1CC0:ce=0,0:cnt=0,0:cap=0,0,0` (снапшот инцидента, не сбрасывался) |
| `mapcap status` | `@MC:STATUS:state=0:term=0:cap=0:frames=0:dropped=0:periods=0:avail=0:detail=0:raw_vbus=0:vbus_mv=0:i1_ma=0:i2_ma=0:adc_status=0:sector=0:window=0` |
| `mapcap identity` | `@MAP:IDENTITY:board=7:pwm=5000:arr=999:trig=0x4F455731:off=0:dt=192:adc_clk=42500000:sample_x2=1281:res=0:acs=0x26B9B97B:ccs=0x13552B12` |
| `mapload 00` | `@MAP:LOAD:FAIL:DECODE` |
| `mapcap build=` | `err: mapcap build=<profile>` |

## Что это доказывает

1. **Команды в образе есть.** `mapcap identity` и `mapcap build=` живут под
   `#if OEW_MAP_CAPTURE && OEW_MAP_L3` (`main.c:543-573`), `mapcap status` — под
   `#if OEW_MAP_CAPTURE`; дефолты обоих макросов — `0` (`main.c:22-26`), значит образ собран
   с `OEW_MAP_CAPTURE=1 && OEW_MAP_L3=1`. `mapload 00` отвечает `@MAP:LOAD:FAIL:DECODE`, а не
   `unknown` ⇒ команда `mapload` скомпилирована.
2. **Прежний вывод B17 был артефактом формы зонда**, а не свойством образа.
3. **`rc=-2` — это «карта ещё не загружена», а не «путь закрыт сборкой».**

## Что ещё выяснилось про путь к карте (проверено по коду)

* `mapcap build=<profile_id>` принимает не произвольный `<N>`, а профиль из allow-list платы:
  `0x424F4152 + sector*2 + window` (12 вариантов, десятичные `1112490322…1112490333`);
  чужой id → `@MC:ARM:BLOCKED:PROFILE` / `@MAP:BUILD:BLOCKED:PROFILE`
  (`src/map_capture_profiles.c:187,261-266`);
* сборка возможна только после завершённого захвата (иначе
  `@MAP:BUILD:BLOCKED:CAPTURE_STATE=…:TERM=…:AVAILABLE=…`) и только при совпадении identity
  профиля с живой (иначе `@MAP:BUILD:BLOCKED:IDENTITY`) — `main.c:336-371`;
* внешние артефакты `мотор анализ\map_upload_pkg_20260908\*.bin` несут `pwm=294` — это дефект
  D3 (двойной учёт `PSC`); такие identity «отвергаются как устаревшие»
  (`src/map_capture_profiles.c:191-199`), а живая плата даёт `pwm=5000` ⇒ `mapload` этих файлов
  закончится `@MAP:LOAD:FAIL:COMMISSION`. На этой плате карта берётся захватом на месте.

## Границы вывода

Захват не запускался: неизвестны ни `@MC:ARM`/`@MC:RUN`, ни качество будущей карты, ни
поведение `1` после загрузки. `mapcap run` — **энергированный** burst из 8 импульсов, поэтому
комиссионинг остаётся действием владельца стенда (по §3 `START_HERE_PC3.md` ему достаточно
24–36 В). Штатный порядок шага точки зафиксирован в `docs/ACCEPTANCE_LESSONS.md` §25.10 и в
runbook `README_BOAR_CAPTURE_PC3.md` §3.

## Воспроизведение

```text
py -3 _bench_capability_probe.py --port COM4 --out docs\evidence\incident_20261004_hwbreak\09_capability_probe_raw.log
```

Файл `09_capability_probe_raw.log` содержит шапку, дословные ответы, строку `# stream`
(кадры 100-мс телеметрии `@FOC` свёрнуты в `@FOC:<frame>`, порядок сохранён — видно, что
ответ не потерян и не подменён) и таблицу вердиктов.
