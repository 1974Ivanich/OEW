"""Общий словарь причин ошибок Auto-Tune и расшифровка кодов AT-ошибок.

Используется nucleo_debug_tool.py (GUI) и test_autotune.py (CLI-прогон).
Коды сверены с фактическими строками, которые шлёт прошивка
(src/autotune.c, main.c).
"""

AT_ERROR_CAUSES = {
    # ch / детекция канала
    "CH_DETECT:ERROR:NO_CURRENT": "нет тока в DC-звене при тестовом импульсе: проверь питание 24В, шунт IN (PA6), подключение мотора",
    "CH:FAIL": "канал тока не определён: проверь I1/I2/IN подключение шунтов и питание",
    # общие
    "ERROR:VBUS_LOW": "низкое напряжение питания VBUS: проверь блок питания (ожидается >= 12В)",
    "ERROR:NONZERO_CURRENT": "ток не равен нулю до старта: проверь смещение ADC (калибровка 'c') и схему",
    # iv (Rs multi-point)
    "RS_IV:ERROR:OVERCURRENT": "превышение тока при измерении Rs: проверь шунт и MaxCurrent",
    "RS_IV:ERROR:TOO_FEW_POINTS": "мало точек для регрессии Rs: проверь диапазон напряжений/токов",
    "RS_IV:ERROR:NO_CURRENT_SPREAD": "нет разброса тока по точкам: проверь питание и шунт IN",
    "IV:FAIL": "измерение Rs провалено: смотри причины выше",
    # pairs (фазы)
    "ERROR:OPEN_PHASE": "обрыв фазы: проверь соединение обмоток мотора с инвертором",
    "ERROR:SHORT": "короткое замыкание фазы: проверь обмотки/изоляцию",
    "PAIRS:ERROR:ALL_FAILED": "все пары фаз не прошли: проверь мотор, питание, шунты",
    "PAIRS:RESULT_FAIL": "разброс по парам > допуска: проверь симметрию обмоток",
    # idle (Ls кривая)
    "IDLE:ERROR:OVERCURRENT": "превышение тока на idle-кривой: проверь шунт и MaxCurrent",
    "IDLE:ERROR:OPEN_PHASE": "обрыв фазы на idle-кривой",
    "IDLE:ERROR:SHORT_OR_LOW_RS": "КЗ или слишком малое Rs: проверь обмотки и измерение Rs",
    # irot / inertia
    "IROT:ERROR:FAULT": "fault при I-f разгоне: проверь ток/напряжение, отправь 'f'",
    "INERTIA:ERROR:FOC_NOT_RUNNING": "FOC не запущен для измерения инерции: запусти FOC сначала",
    # oew
    "OEW:ERROR:OVERCURRENT": "превышение тока в OEW-тесте: проверь шунт и MaxCurrent",
    # rr (роторное сопротивление)
    "RR:ERROR:RS_NOT_MEASURED": "Rs не измерена: сначала выполни iv/pairs",
    "RR:ERROR:OVERCURRENT": "превышение тока в Rr-тесте",
    "RR:ERROR:NO_CURRENT": "нет тока в Rr-тесте: проверь питание и шунт IN",
    # noload
    "NOLOAD:ERROR:LS_NOT_MEASURED": "Ls не измерена: сначала выполни idle",
    "NOLOAD:ERROR:NO_CURRENT": "нет тока в no-load тесте: проверь питание и подключение",
    # mp / pi
    "MP:ERROR": "параметры не применены в FOC: проверь что FOC остановлен",
    "PI:ERROR:NOT_CALCULATED": "PI не рассчитан: сначала выполни iv/idle для Rs/Ls",
    "PI:ERROR": "ошибка применения PI-коэффициентов",
    # lspos
    "LSPOS:ERROR": "ошибка измерения Ls(θ): проверь питание и шунт",
    "LSPOS:RESULT_FAIL": "разброс Ls(θ) > допуска: проверь симметрию обмоток",
}


def at_error_cause(line):
    """Вернуть человекочитаемую причину ошибки AT-команды по коду из прошивки."""
    if ":ABORTED" in line or line.startswith("Abort"):
        return "  └─ Причина: прервано пользователем (abort) — измерение остановлено, PWM отключён"
    if "ERROR:FAULT" in line:
        return "  └─ Причина: активен fault-флаг: отправь 'f' для сброса (protect.c) — проверь ток/напряжение"
    for code, cause in AT_ERROR_CAUSES.items():
        if code in line:
            return f"  └─ Причина: {cause}"
    return "  └─ Причина: неизвестный код ошибки (смотри autotune.c/main.c)"
