# ТЗ: GUI-логирование — P1+P2 (batching, retention, session-надёжность)

**Источник:** read-only аудит телеметрии/логирования (Manus, на `0dd7f4a`).
Все пункты подтверждены приёмщиком в коде (`vf_panel.py`, `nucleo_debug_tool.py`,
`measurement_gui.py`, `foc_control_gui.py`).

## Требования

1. **P1 — V/f CSV batching** (`vf_panel.py:319–328`): заменить per-row `flush()` на batched
   (каждые N=10–25 строк / 200–500 мс) + обязательный flush+close в `_close_session()`/стоп.
   Опциональный явный `fsync`-режим с задокументированной ценой — только как отдельная опция.
   **Не удалять flush молча** — с заменой и lifecycle-тестом.
2. **P1 — bounded Tk logs** (`nucleo_debug_tool._log`, `foc_control_gui._log`, `measurement_gui._log`):
   retention limit (2 000–10 000 видимых строк) с bulk trim; autoscroll (`see(END)`) — только если
   view уже был в конце. Полная история остаётся в on-disk log/export — потеря forensic-данных нет.
3. **P1 — buffered file writer** (`NucleoDebugTool._log_to_file`): синхронный write+flush на строку →
   `queue.Queue` + выделенный файловый worker (или GUI-thread flush timer). Очередь bounded;
   политика: telemetry может coalesce/drop со счётчиком, errors/commands/state — lossless (high priority).
   Close: drain+flush до destroy.
4. **P2 — Measurement queue** (`measurement_gui.py:281–291`): обычный list + `pop(0)` → паттерн
   `queue.Queue` + `get_nowait()` (как в FOC GUI). FIFO без потерь при burst.
5. **P2 — session-директории и write-ошибки** (`vf_panel.py:145–148, 319–328`): уникальность имени
   (микросекунды или суффикс `session_id`) + fail-closed, если директория неожиданно непустая;
   при первом `OSError` — закрыть/отключить writer, сохранить причину в session meta/status,
   без повторных исключений на каждой строке.

## Регрессии (pytest)

- `tmp_path`: все ожидаемые строки доходят до CSV после stop; инжектированный write-failure
  меняет GUI/session-состояние ровно один раз.
- Log retention: вставка > limit → bounded Text, on-disk log содержит все записи.
- Session naming: два старта в одну «секунду» (mock) → разные директории, независимые CSV/meta.
- Measurement queue: burst reader/consumer → FIFO, без потерь, без O(n) list-пути.

## Запреты

- Wire-протокол не менять (GUI только под фактический протокол прошивки).
- `src/*`, `.ioc`, fail-closed — 0 строк. Только 4 GUI-модуля + тесты.
- P2 firmware (cadence ownership — подавление `@VF` при `vflog`) — вне рамок, требует отдельного ТЗ.
- P3 (SWO diagnostics, parser micro-оптимизации) — вне рамок.

## Приёмка

`py_compile` 4 GUI-модулей PASS; `pytest tests -q` ALL PASS (19 + новые); `make test` ALL PASS
(прошивка не меняется); `git diff --check`; CI ветки success; P0-совместимость GUI (CRLF framing,
`Ires`-телеметрия) — регрессии зелёные.
