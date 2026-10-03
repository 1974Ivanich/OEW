# Пакет FOC 48-ячеечного скана (ПК-3), ред. 2

Пакет для 48-ячеечного скана: 12 углов × 4 амплитуды, метрика — прирост счётчика `JEOS`
(`ΔJEOS`) за 2-секундное окно молчания. Прошивка не меняется и не перепрошивается:
счётчик уже есть в замороженном образе на плате (`ab1274d`, заявленный образ `d2bc0439…`).

Ред. 2 заменяет ред. 1 (передавалась одним ZIP без сайдкара): исправлены 10 дефектов,
из-за которых ред. 1 **нельзя было запускать** на стенде, — шкала метрики уезжала в 8.5 раза
(ложный L2), автостоп по `FAULT` был мёртв, амплитуды вообще не отправлялись.
Полный разбор с ссылками на исходник: [CORRECTIONS_v2.md](CORRECTIONS_v2.md).

## Состав

| файл | зачем |
|---|---|
| `START_HERE_PC3.md` | порядок работ: identity → baseline → блок амплитуд → угол → возврат |
| `COMMANDS.md` | все команды сценария (включая `--dry-run`) |
| `FOC_SCAN_48CELL_PROTOCOL.md` | замороженный протокол: identity, декод, сетка, уровни L0–L5, стоп-правила |
| `IDENTITY_AND_BASELINE.md` | форма заполнения identity + baseline-декода |
| `RETURN_TEMPLATE.md` | форма возврата (колонки = колонки `cells.csv`) |
| `CORRECTIONS_v2.md` | дефекты ред. 1 и правки ред. 2 (с доказательствами) |
| `TOOLS/calc_expected.py` | шкала метрики: `f_cnt/f_pwm/f_UEV/f_JEOS/N_expected` + две сверки |
| `TOOLS/scan_48cell.py` | драйвер сессии: ячейки, окно только-чтение, автостопы, CSV/лог/summary |
| `TOOLS/check_package.py` | самопроверка пакета (свойства, а не «файлы на месте») |
| `SHA256SUMS` | хеши всех файлов пакета (кроме самого `SHA256SUMS`) |

## Требования

* Python 3.8+ (проверено на `py -3`); для реального прогона — `pyserial` (`pip install pyserial`);
* плата с замороженным образом; перед клетками обязателен baseline-декод (иначе `--n-expected`
  взять неоткуда и драйвер отказывается работать).

## Проверка пакета (обязательно перед стендом)

```bash
py -3 TOOLS/check_package.py
```

Проверяется: состав, покрытие `SHA256SUMS` (без пропусков и лишних строк), отсутствие CRLF/BOM,
контрольные числа шкалы (`f_pwm=5000`, `f_JEOS=5000`, `N_expected(2 с)=10000`), отказ на
подстановке `TCLK` вместо `CLK` (класс ошибки ред. 1), инварианты драйвера
(`--dry-run`: `f` среди команд = 0 и `i=` строго после `1`). Ожидание — `TOTAL: PASS`.

## Сборка ZIP (для отправителя)

```bash
py -3 scripts/build_foc_scan_48cell_pkg.py                 # manifest + check + ZIP + .sha256
py -3 scripts/build_foc_scan_48cell_pkg.py --verify build/FOC_SCAN_48CELL_PC3_V2_<дата>.zip
```

Сборка fail-closed: если `check_package.py` не PASS, ZIP не создаётся. ZIP детерминирован
(фиксированное время записей, сортировка, forward slashes) — один и тот же вход даёт один и
тот же SHA-256; рядом кладётся `<zip>.sha256` (`sha256sum`-формат), его нужно отправлять вместе
с ZIP.

Если `check_package.py` печатает `CRLF FOUND` (рабочее дерево лежит на машине с
`core.autocrlf=true`), сначала лечится байты явным режимом:

```bash
py -3 scripts/build_foc_scan_48cell_pkg.py --normalize --manifest
```

`__pycache__/` и `*.pyc` в пакет не попадают (исключены и в упаковщике, и в `.gitignore`).

## Запуск сессии

Кратко (полностью — `START_HERE_PC3.md`):

```bash
py -3 TOOLS/calc_expected.py --clk 170000000 --psc 16 --arr 999 --cms 3 --rcr 1 --tclk 10000000 --window 2.0
py -3 TOOLS/scan_48cell.py --port COM4 --amp 2000,3000,4000,5000 --angles-el 0 --window 2.0 --n-expected 10000 --out .\run1 --dry-run
py -3 TOOLS/scan_48cell.py --port COM4 --amp 2000,3000,4000,5000 --angles-el 0 --window 2.0 --n-expected 10000 --out .\run1
```

Результат прогона — `cells.csv`, `session_raw.log`, `summary.txt`, `f_list.txt`.
Автостоп при: нет `FOC started` (в т.ч. `rc=-5` = не energize-сборка), нет `@I:OK`,
`FAULT`/`FAULT_R != 0`, `em_stop=0` (линия SD в аварии).
