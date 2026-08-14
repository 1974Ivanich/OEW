# -*- coding: utf-8 -*-
"""Фиксы аудита сборки/startup/GUI: BSP-04 init_array секции в linker.ld,
BSP-05 _Min_Stack_Size + heap ASSERT, GUI-13 vfk= перед vf= в _vf_start,
GUI-14 @VF:STOPPED:REASON=FAULT (прошивка) + on_stopped в vf_panel (close
session + meta end_reason), GUI-17 безопасные тексты at_errors.py.
BSP-01 уже исправлен (c1a5981), BSP-02/03 опровергнуты (собирается),
GUI-15 опровергнут (voltage_mag — проценты). CRLF-safe."""
import io, sys

def edit(path, pairs):
    with io.open(path, 'r', encoding='utf-8', newline='') as f:
        text = f.read()
    eol = '\r\n' if '\r\n' in text else '\n'
    for old, new in pairs:
        old = old.replace('\n', eol)
        new = new.replace('\n', eol)
        n = text.count(old)
        if n == 0:
            print(f"[FAIL] 0 совпадений в {path}: {old[:70]!r}")
            sys.exit(1)
        text = text.replace(old, new)
        print(f"[OK] {n} в {path}: {old[:45]!r}")
    with io.open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text)
    print(f"[DONE] {path}")

# ───────────────────────── linker.ld (BSP-04/05) ─────────────────────────
edit('linker.ld', [
    ("""_estack = ORIGIN(RAM) + LENGTH(RAM);""",
     """_estack = ORIGIN(RAM) + LENGTH(RAM);

/* Ревью BSP-05: резерв под стек (растёт вниз от _estack). ASSERT ниже
 * проверяет, что data/bss/heap не пересекаются с зарезервированным
 * стеком — иначе переполнение стека тихо портит глобальные переменные. */
_Min_Stack_Size = 0x1000;"""),
    ("""    .ARM.attributes 0 : { *(.ARM.attributes) }
}""",
     """    /* Ревью BSP-04: стандартные секции конструкторов — newlib
     * __libc_init_array (вызывается из startup) использует эти границы. */
    .preinit_array :
    {
        . = ALIGN(4);
        __preinit_array_start = .;
        KEEP (*(.preinit_array*))
        __preinit_array_end = .;
    } > FLASH
    .init_array :
    {
        . = ALIGN(4);
        __init_array_start = .;
        KEEP (*(SORT(.init_array.*)))
        KEEP (*(.init_array*))
        __init_array_end = .;
    } > FLASH
    .fini_array :
    {
        . = ALIGN(4);
        __fini_array_start = .;
        KEEP (*(SORT(.fini_array.*)))
        KEEP (*(.fini_array*))
        __fini_array_end = .;
    } > FLASH

    .ARM.attributes 0 : { *(.ARM.attributes) }
}"""),
    ("""    . = ALIGN(4);
    _end = .;
    __end__ = .;
    end = .;
""",
     """    . = ALIGN(4);
    _end = .;
    __end__ = .;
    end = .;

    /* Ревью BSP-05: границы heap (_sbrk) и ASSERT пересечения с резервом
     * стека. Фактический high-water mark стека — измерить на стенде. */
    . = ALIGN(8);
    __HeapBase = _end;
    __HeapLimit = ORIGIN(RAM) + LENGTH(RAM) - _Min_Stack_Size;
    ASSERT(__HeapLimit >= __HeapBase, "RAM overflow: data/bss/heap intersects stack")
"""),
])

# ───────────────────────── main.c (GUI-14: @VF:STOPPED) ─────────────────────────
edit('main.c', [
    ("""            if(PROTECT_IsFault()) { VFC_Stop(); vflog_period_ms = 0; TRIG_Low(); }""",
     """            if(PROTECT_IsFault()) {
                VFC_Stop(); vflog_period_ms = 0; TRIG_Low();
                /* Ревью GUI-14: уведомление GUI о принудительной остановке
                 * V/f — GUI закрывает CSV-сессию с reason (иначе файл и
                 * session остаются активными после fault). */
                UART_TrySendTelemetry("@VF:STOPPED:REASON=FAULT\\r\\n");
            }"""),
])

# ───────────────────────── vf_panel.py (GUI-13/14) ─────────────────────────
edit('vf_panel.py', [
    # GUI-13: vfk= ДО vf= (порядок в UART FIFO сохраняется)
    ("""        self._start_session(rpm, boost, rated)
        if self.saleae is not None and getattr(self.saleae, 'available', False):
            threading.Thread(target=self._capture_sigrok, daemon=True).start()
        self.send(f"vf={rpm}")   # MCU поднимет PB6 и пришлёт @TRIG:tick=... сразу после этого""",
     """        self._start_session(rpm, boost, rated)
        if self.saleae is not None and getattr(self.saleae, 'available', False):
            threading.Thread(target=self._capture_sigrok, daemon=True).start()
        # Ревью GUI-13: параметры применяются ДО старта (порядок команд в
        # UART FIFO сохраняется) — раньше vfk= уходил только из Spinbox-
        # callback'а, асимметрично и без гарантии применения к этому старту.
        self.send(f"vfk={boost},{rated}")
        self.send(f"vf={rpm}")   # MCU поднимет PB6 и пришлёт @TRIG:tick=... сразу после этого"""),
    # GUI-14: _close_session(reason) + meta end_reason
    ("""    def _close_session(self):
        if self.csv_fp is not None:
            try:
                self.csv_fp.close()
            except OSError:
                pass
            if self.session_dir:
                self.log_status_label.config(
                    text=f"Log: {os.path.basename(self.session_dir)} saved ({self.vflog_count} pts)",
                    foreground="blue")
        self.csv_fp = None
        self.csv_writer = None""",
     """    def _close_session(self, reason=""):
        if self.csv_fp is not None:
            try:
                self.csv_fp.close()
            except OSError:
                pass
            if self.session_dir:
                # Ревью GUI-14: итог сессии — end_time/end_reason/samples.
                try:
                    mp = os.path.join(self.session_dir, "meta.json")
                    with open(mp, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    meta["end_time"] = datetime.now().isoformat()
                    meta["end_reason"] = reason or "GUI_STOP"
                    meta["samples"] = self.vflog_count
                    with open(mp, "w", encoding="utf-8") as f:
                        json.dump(meta, f, indent=2)
                except (OSError, ValueError):
                    pass
                self.log_status_label.config(
                    text=f"Log: {os.path.basename(self.session_dir)} saved ({self.vflog_count} pts)",
                    foreground="blue")
        self.csv_fp = None
        self.csv_writer = None

    def on_stopped(self, line):
        \"\"\"Ревью GUI-14: V/f остановлен прошивкой (fault/remote) — закрыть
        CSV-сессию и показать reason. Вызывается из main GUI thread.\"\"\"
        reason = line.split("REASON=", 1)[1].strip() if "REASON=" in line else "UNKNOWN"
        self._close_session(reason)
        self.vf_status_label.config(text=f"V/f stopped by MCU: {reason}", foreground="red")"""),
])

# ───────────────────────── nucleo_debug_tool.py (GUI-14: маршрутизация) ─────────────────────────
edit('nucleo_debug_tool.py', [
    ("""    def _on_line(self,line):
        print(f"[UART] {line}")
        self._log(line,"received")
        # AutoTuneTab handles @IDLE:*, @PARAMS:*, @IROT:*, @INERTIA:*
        if self.tab_at.on_line(line):
            return""",
     """    def _on_line(self,line):
        print(f"[UART] {line}")
        self._log(line,"received")
        # Ревью GUI-14: V/f принудительно остановлен прошивкой — закрыть сессию.
        if line.startswith("@VF:STOPPED"):
            self.vf_panel.on_stopped(line)
            return
        # AutoTuneTab handles @IDLE:*, @PARAMS:*, @IROT:*, @INERTIA:*
        if self.tab_at.on_line(line):
            return"""),
])

# ───────────────────────── at_errors.py (GUI-17) ─────────────────────────
edit('at_errors.py', [
    ("""        "    1. @DBG:CH:PRE_TEST:FAULT / @DBG:CH:POST_DELAY:FAULT=1 -> PROTECT сработал, ШИМ выключен ('f' для сброса)\\n\"""",
     """        "    1. @DBG:CH:PRE_TEST:FAULT / @DBG:CH:POST_DELAY:FAULT=1 -> PROTECT сработал, ШИМ выключен\\n"""
     """        \"       (сброс fault — только после устранения причины: request-clear\\n\"""",
     """        "       проверяет Vbus/токи по свежим данным — см. Clear fault)\\n\""""),
    ("""    "IROT:ERROR:FAULT": "fault при I-f разгоне: проверь ток/напряжение, отправь 'f',""",
     """    "IROT:ERROR:FAULT": "fault при I-f разгоне: проверь ток/напряжение; сброс fault —""",
     ),
    ("""        return "  └─ Причина: активен fault-флаг: отправь 'f' для сброса (protect.c) — проверь ток/напряжение\"""",
     """        return "  └─ Причина: активен fault-флаг — устрани первопричину, затем Clear fault (прошивка проверит Vbus/токи по свежим данным)\""""),
])

# Makefile: --print-memory-usage (BSP-05 рекомендация)
edit('Makefile', [
    ("""LDFLAGS = $(CPU_FLAGS) -Tlinker.ld -Wl,-Map=$(BUILD_DIR)/$(TARGET).map""",
     """LDFLAGS = $(CPU_FLAGS) -Tlinker.ld -Wl,-Map=$(BUILD_DIR)/$(TARGET).map -Wl,--print-memory-usage"""),
])

print("ALL DONE")
