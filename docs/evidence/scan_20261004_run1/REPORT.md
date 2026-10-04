# Прогон run1 (2026-10-04): живой блок амплитуд — автостоп на гейте параметров

Команда (по `COMMANDS.md`, шаг 3 пакета):

```
py -3 TOOLS/scan_48cell.py --port COM4 --amp 2000,3000,4000,5000 --angles-el 0 \
    --window 2.0 --n-expected 10000 --out .\run1
```

Предусловия (выполнены):

* `--dry-run` — `cells: 4`, `commands total: 28; "f" among them: 0`;
* латч `PROTECT_FAULT_HARDWARE_BREAK` снят штатно: `c` (`offset_i1=2038`) → `f`
  (`FAULT=0:FAULT_R=0`); выходы обесточены (`p? CR1=224:CCER=0:BDTR=7360`, MOE=0);
* питание звена подано: `@FOC:VBUS=62454` мВ, `a` → `@ADC:VBUS=619` raw (~62 В).

## Результат

`cells.csv` (1 строка), `summary.txt`, `session_raw.log`, `f_list.txt` (= пусто):

| cell | amp_ma | angle_el | jeos_before | jeos_after | d_jeos | level | fault | fault_r | start_reply |
|---|---|---|---|---|---|---|---|---|---|
| A2000_ANG0 | 2000 | 0 | 0 | 0 | 0 | L0 | 0 | 0 | `FOC start blocked: motor Rs/Ls out of sane range (run autotune, then mp=/mpapply) @FOC:START:FAIL:rc=-6 …` |

`summary.txt`:

```
cells: 1 of 4
levels: L0=1
N_expected over window 2.00 s: 10000.0
median dJEOS/N_expected: 0.000
decode cross-check: ratio 0.00: decode not confirmed by data
"f" sent: 0
abort: cell A2000_ANG0: FOC did not start (reply: … rc=-6 …)
```

## Разбор

* **Автостоп сработал штатно**: `start_reply` не содержит `FOC started` → `stop_reason()`
  прекратил серию до отправки `i=`/окна; `"f" among them: 0`, PWM не включался
  (`p?` после прогона — `CCER=0`, `BDTR=7360` MOE=0; `@FOC:RUN=0:FAULT=0`).
* **Код отказа `rc=-6` = `params_out_of_range`** (`src/foc.h:50`, `src/foc.c:552-556`):
  дефолтные параметры (`FOC_DEFAULT_R_MOHM=13000`, `FOC_DEFAULT_L_UH=100`, `src/foc.c:184-185`)
  fail-closed — `L=100 мкГн` намеренно ниже `AT_MATH_SANE_LS_MIN_UH=500 мкГн`
  (`src/foc.c:190-192` — статическая проверка «дефолт обязан быть вне окна»).
  Окно гейта: Rs 10…100000 мОм, Ls 500…500000 мкГн (`src/autotune_math.h:11-14`).
* Следующий гейт (сработал бы после `mp=`): `CurrentRecon_IsReady()` → `rc=-2 map_unverified`
  (`src/foc.c:560-563`); в телеметрии карты нет (`map_id=M0`, `map_crc32=00000000`),
  `mapcap identity` доступен, `mapload` → `unknown`.

## Вывод

48-ячеечный скан на текущей плате **невыполним без подготовки**: нужны
явные `mp=<Rs>,<Ls>` + `mpapply` и построенная карта (`mapcap build=<N>`).
Пакет `foc_scan_48cell_v2` таких шагов не содержит (и `mapcap`/`mp=` не входят
в разрешённый набор команд стенда). Это отдельная (комиссионинг) работа.

Ячейка `A2000_ANG0`, `d_jeos=0`, `level=L0` — **не** результат FOC: PWM в этой
попытке не включался, метрика не измерялась. В возврат это идёт как «0 клеток».
