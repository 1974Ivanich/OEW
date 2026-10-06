# ТЗ: GUI-фиксы P0+P1 (wire-протокол + конкурентность)

**Источник:** расширенный аудит GUI (Manus, 2026-08-21), подтверждён приёмщиком.
**Объём:** только P0 + P1. P2 — вне рамок (очереди/generations — отдельным пакетом).
**Затрагиваемые файлы:** `measurement_gui.py`, `foc_control_gui.py`,
`nucleo_debug_tool.py`, `vf_panel.py` (только если потребуется для P1-1),
`tests/` (новые pytest-регрессии).
**ЗАПРЕЩЕНО:** `src/*` (прошивка), `.ioc`, safety-модули, wire-протокол
прошивки (меняем GUI под фактический протокол, не наоборот).

## P0 — wire-протокол (обязательно, блокирующие)

### P0-1. `measurement_gui.py:225–234` — отсутствие терминатора
`_send_cmd()` выполняет `self.ser.write(cmd.encode())` без `\r`/`\n`.
Production `UART_ReadLine()` (`src/uart.c:220–254`) исполняет команду только
по CR/LF — команды `1/2/3/Q` (Phase U/V/W, Cycle) не исполняются, байты
склеиваются. Фикс: `self.ser.write((cmd + "\r\n").encode())`.
Критерий: pytest воспроизводит `measurement_payload == [b'1\r\n']` (мок serial).

### P0-2. `foc_control_gui.py:29` — regex не соответствует телеметрии
`TELEMETRY_RE` ожидает `@FOC:...:IN=...`, прошивка шлёт
`@FOC:I1=...:I2=...:Ires=...:VBUS=...:STATE=...` (`main.c:611`). GUI не
распознаёт штатную FOC-телеметрию. Фикс: regex под фактический формат
(`Ires=` вместо `IN=`; учесть `STATE/SPD/TH/FAULT/...`), порядок полей —
как в `main.c:611`.
Критерий: pytest `foc_regex_matches_production == True` (regex против
эталонной строки из main.c:611).

## P1 — конкурентность (обязательно)

### P1-1. `nucleo_debug_tool.py:88–184, 277–296` — SaleaeHelper без lock
Один `_sigrok_tmp`, общий `_tr_cache`, нет владения захватом: параллельные
кнопки → `_clean_tmp()` удаляет каталог чужого захвата, `measure_voltage()`
чистит его в `finally`. Фикс: сериализация захватов (lock/ownership;
один активный capture; tmp-каталог привязан к захвату, чужие не удаляются).

### P1-2. `nucleo_debug_tool.py:543–650` — PWM worker и Tkinter
Worker-потоки читают `IntVar.get()`, `BooleanVar`, `winfo_toplevel()` и
логируют в Tk `Text` из потока (Tk не потокобезопасен → `TclError`,
ложный PASS/FAIL auto-test). Фикс: snapshot конфигурации (mask, значения)
ДО старта потока; из worker — только queue; Tk-обновления — через
`root.after`/очередь в главном потоке.

### P1-3. `foc_control_gui.py:405–417` — гонка copy/clear job-очереди
`jobs = self._gui_jobs[:]` затем `clear()` — job, добавленная между ними,
теряется. Фикс: `queue.Queue` (или иной явный потокобезопасный контракт).

### P1-4. `foc_control_gui.py:243–253, 393–401` — оптимистичный `foc_active`
`foc_active` выставляется при отправке `1`/`0`, а не по подтверждению
телеметрии (`RUN`/`STATE`). Фикс: состояние UI по фактической телеметрии
(fail-closed запуск/fault → GUI не показывает RUNNING).

## Требования к реализации

1. Ветка `ai4/gui-p0-p1-fixes` от **свежего** `origin/main`
   (`git fetch && git checkout -b ... origin/main`); запись в
   `docs/AGENTS_STATUS.md` до старта.
2. Pytest-регрессии на P0 (мок serial/протокол), на P1 — где
   воспроизводимо (конкурентность моками), остальное — smoke + py_compile.
3. Не менять поведение прошивки и её wire-протокол.
4. Публикация: `git push -u origin ai4/gui-p0-p1-fixes` + подтверждение
   SHA через `git ls-remote origin refs/heads/ai4/gui-p0-p1-fixes`.

## Приёмка (чек-лист приёмщика)

- [ ] `python -m py_compile` всех 4 GUI-модулей + `telem_parser.py`
- [ ] `pytest tests/` — все тесты (включая новые) PASS
- [ ] `make test` — TESTS OK (прошивка не менялась — должен быть зелёным)
- [ ] flake8 `--select=F821` — чисто
- [ ] `git diff origin/main...HEAD --check` — чисто
- [ ] P0-1/P0-2 критерии (payload `b'1\r\n'`, `foc_regex_matches_production`)
- [ ] `src/` и `.ioc` — 0 строк diff
- [ ] CI ветки — зелёный
