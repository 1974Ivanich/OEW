#!/usr/bin/env python3
"""Простая консольная утилита для проекта тестирования датчиков тока STEVAL-IPM20B.

Без GUI, без графиков — только прямой обмен данными по UART для отладки датчиков тока.

Использование:
    python sense_test.py COM12

Команды (введите и нажмите Enter):
    calibrate      управляемая калибровка с помощью ручного мультиметра
    adc            однократное чтение сырых значений АЦП
    adccont        непрерывный вывод значений АЦП
    adcstop        остановить непрерывный вывод
    calib          калибровать смещения нулевого тока
    i              вычислить/вывести токи
    dc A 850       DC-тест фазы A с duty 850
    stop           остановить DC-тест
    scaleA 0.00035  задать коэффициент масштабирования
    help           список команд
    q              выход
"""
import argparse
import csv
import json
import queue
import re
import statistics
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import serial
import serial.tools.list_ports


class CalibrationAborted(Exception):
    pass


RAW_CHANNELS = ("A1", "B1", "C1", "A2", "B2", "C2")
PHASE_CHANNELS = {"A": ("A1", "A2"), "B": ("B1", "B2"), "C": ("C1", "C2")}
RESISTANCE_PHASES = ("A", "B", "C")
DEFAULT_CONFIG = {
    "calibration_duties": [-3000, -2500, 2500, 3000],
    "verification_duties": [-2500, 3000],
    "sample_count": 50,
    "min_adc_delta_lsb": 5.0,
    "noise_multiplier": 10.0,
    "verification_tolerance_percent": 5.0,
    "linearity_tolerance_percent": 5.0,
    "resistance_duty": 1500,
    "pwm_period": 8500,
    "output_dir": "reports",
}
MAX_INTERACTIVE_DC_SECONDS = 20.0


class SerialConsole:
    def __init__(self, port, uart_log_path):
        self.port = port
        self.ser = serial.Serial(port, 115200, timeout=0.1)
        self.stop_event = threading.Event()
        self.lines = queue.Queue()
        self._log_lock = threading.Lock()
        self._uart_log = uart_log_path.open("w", encoding="utf-8", buffering=1)
        self.thread = threading.Thread(target=self._reader, daemon=True)
        self.thread.start()

    def _log(self, direction, line):
        with self._log_lock:
            self._uart_log.write("%s %s %s\n" % (datetime.now().isoformat(timespec="milliseconds"), direction, line))

    def _reader(self):
        while not self.stop_event.is_set():
            try:
                line = self.ser.readline().decode("ascii", errors="ignore").strip()
                if line:
                    self._log("RX", line)
                    print(line)
                    self.lines.put(line)
            except Exception as error:
                self._log("RX_ERROR", str(error))
                print("[ошибка чтения]", error)
                break

    def send(self, command):
        self._log("TX", command)
        self.ser.write((command + "\r").encode("ascii"))

    def clear_lines(self):
        while True:
            try:
                self.lines.get_nowait()
            except queue.Empty:
                return

    def request(self, command, prefix, timeout=5.0):
        self.clear_lines()
        self.send(command)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=deadline - time.monotonic())
            except queue.Empty:
                break
            if line.startswith(prefix):
                return line
        raise TimeoutError("Нет ответа на %r в течение %.1f с" % (command, timeout))

    def close(self):
        try:
            self.send("stop")
        except Exception:
            pass
        self.stop_event.set()
        self.thread.join(timeout=0.5)
        self.ser.close()
        self._uart_log.close()


def parse_fields(line):
    return {key: float(value) for key, value in re.findall(r"([A-Za-z0-9_]+)=(-?\d+(?:\.\d+)?)", line)}


def read_current_magnitude(prompt):
    while True:
        value = input(prompt).strip().replace(",", ".")
        if value.lower() in {"abort", "a", "q", "quit"}:
            raise CalibrationAborted()
        try:
            current = float(value)
        except ValueError:
            print("Введите положительный ток в амперах, например 0.70, или введите abort.")
            continue
        if current <= 0:
            print("Введите положительный ненулевой ток, или введите abort.")
            continue
        return current


def sign_from_raw(raw_value, center=2048):
    """Определяет знак тока по уровню RAW АЦП относительно центра (2048)."""
    return -1.0 if raw_value < center else 1.0


def wait_for_ready(prompt):
    value = input(prompt).strip().lower()
    if value in {"abort", "a", "q", "quit"}:
        raise CalibrationAborted()


def read_bus_voltage(prompt):
    while True:
        value = input(prompt).strip().replace(",", ".")
        if value.lower() in {"abort", "a", "q", "quit"}:
            raise CalibrationAborted()
        try:
            voltage = float(value)
        except ValueError:
            print("Введите положительное напряжение шины в вольтах, или введите abort.")
            continue
        if voltage <= 0:
            print("Введите положительное напряжение шины в вольтах, или введите abort.")
            continue
        return voltage


def applied_line_voltage(bus_voltage, duty, pwm_period):
    return bus_voltage * 2.0 * abs(duty) / pwm_period


def stop_continuous_adc(console):
    console.request("adcstop", "ADC continuous OFF", timeout=2.0)


def raw_samples(console, count):
    samples = []
    for _ in range(count):
        fields = parse_fields(console.request("adc", "RAW,", timeout=2.0))
        missing = [channel for channel in RAW_CHANNELS if channel not in fields]
        if missing:
            raise ValueError("Неполный ответ RAW; отсутствуют каналы: %s" % ", ".join(missing))
        samples.append({channel: fields[channel] for channel in RAW_CHANNELS})
    return samples


def robust_noise(values):
    median = statistics.median(values)
    mad = statistics.median(abs(value - median) for value in values)
    return median, 1.4826 * mad


def summarize_samples(samples):
    centers = {}
    noise = {}
    for channel in RAW_CHANNELS:
        centers[channel], noise[channel] = robust_noise([sample[channel] for sample in samples])
    return centers, noise


def stop_dc_test(console):
    try:
        console.request("stop", "STOPPED", timeout=2.0)
    except TimeoutError:
        console.send("stop")


def measure_phase_for_calibration(console, phase, duty, config):
    wait_for_ready("Подключите мультиметр к фазе %s для duty %d, затем нажмите Enter: " % (phase, duty))
    console.request("dc %s %d" % (phase, duty), "DC phase=", timeout=2.0)
    started_at = time.monotonic()
    try:
        magnitude = read_current_magnitude(
            "DC-тест активен. Введите модуль тока мультиметра в фазе %s [А] в течение %d секунд: " % (phase, int(MAX_INTERACTIVE_DC_SECONDS))
        )
        if time.monotonic() - started_at > MAX_INTERACTIVE_DC_SECONDS:
            raise TimeoutError("Время ожидания ввода DC истекло; сторожевой таймер прошивки мог остановить тест")
        centers, noise = summarize_samples(raw_samples(console, config["sample_count"]))
        # Знак тока определяется по знаку duty, так как для OEW шунты A1/A2
        # лежат в разных плечах фазы и дают противоположные отклики.
        current = magnitude * (1.0 if duty > 0 else -1.0)
        return {"duty": duty, "meter_current": current, "raw_median": centers, "raw_noise_mad": noise}
    finally:
        stop_dc_test(console)


def calculate_scale(points, offset, zero_noise, channel, config):
    numerator = 0.0
    denominator = 0.0
    prepared_points = []
    skipped_points = []
    # Для OEW шунты ADC1 измеряют отрицательный ток, ADC2 — положительный.
    adc_index = int(channel[-1])
    for measurement in points:
        duty = measurement["duty"]
        if adc_index == 1 and duty > 0:
            continue
        if adc_index == 2 and duty < 0:
            continue
        delta = measurement["raw_median"][channel] - offset["off_" + channel.lower()]
        effective_noise = max(zero_noise[channel], measurement["raw_noise_mad"][channel])
        minimum_delta = max(config["min_adc_delta_lsb"], config["noise_multiplier"] * effective_noise)
        if abs(delta) < minimum_delta:
            skipped_points.append((measurement["duty"], abs(delta), minimum_delta))
            continue
        numerator += delta * measurement["meter_current"]
        denominator += delta * delta
        prepared_points.append((measurement["duty"], delta, measurement["meter_current"]))
    if skipped_points:
        print("  Предупреждение: для %s пропущены точки с недостаточным откликом:" % channel)
        for duty, delta, threshold in skipped_points:
            print("    duty %d: %.2f LSB < %.2f LSB" % (duty, delta, threshold))
    if not prepared_points:
        raise ValueError("У канала %s нет пригодных точек калибровки" % channel)
    scale = numerator / denominator
    residuals = []
    for duty, delta, current in prepared_points:
        predicted = scale * delta
        residual = predicted - current
        residual_pct = residual / abs(current) * 100.0
        residuals.append({"duty": duty, "measured_a": current, "predicted_a": predicted,
                          "residual_a": residual, "residual_pct": residual_pct})
    max_residual_pct = max(abs(item["residual_pct"]) for item in residuals) if residuals else 0.0
    status = "PASS"
    if len(prepared_points) == 1:
        status = "PASS (single point)"
    elif max_residual_pct > config["linearity_tolerance_percent"]:
        status = "FAIL"
    return scale, {"residuals": residuals, "max_residual_pct": max_residual_pct,
                   "status": status}


def apply_scale_factors(console, scales):
    for command, value in scales.items():
        console.request("%s %.8f" % (command, value), command + "=", timeout=2.0)


def verify_scale_factors(console, config):
    verification = {}
    phase = "A"
    for duty in config["verification_duties"]:
        wait_for_ready("Подключите мультиметр к фазе %s для проверочного duty %d, затем нажмите Enter: " % (phase, duty))
        console.request("dc %s %d" % (phase, duty), "DC phase=", timeout=2.0)
        started_at = time.monotonic()
        try:
            wait_for_ready("DC-тест активен. Подождите стабилизации показаний мультиметра, затем нажмите Enter: ")
            if time.monotonic() - started_at > MAX_INTERACTIVE_DC_SECONDS:
                raise TimeoutError("Время ожидания ввода DC истекло; сторожевой таймер прошивки мог остановить тест")
            currents = parse_fields(console.request("i", "I,", timeout=2.0))
            magnitude = read_current_magnitude("Введите модуль тока мультиметра в фазе %s [А]: " % phase)
            if time.monotonic() - started_at > MAX_INTERACTIVE_DC_SECONDS:
                raise TimeoutError("Время ожидания ввода DC истекло; сторожевой таймер прошивки мог остановить тест")
            # Для OEW знак тока совпадает со знаком duty: отрицательный duty ->
            # ток через шунт ADC1 (ia1), положительный -> через ADC2 (ia2).
            sign = 1.0 if duty > 0 else -1.0
            meter_current = magnitude * sign
            phase_result = {"multimeter_a": meter_current}
            active_adc = 1 if duty < 0 else 2
            current_key = "i%s%d" % (phase.lower(), active_adc)
            if current_key not in currents:
                raise ValueError("Неполный ответ о токах; отсутствует %s" % current_key)
            passed = True
            print("\nПроверочный duty %d, фаза %s:" % (duty, phase))
            calculated = currents[current_key]
            difference = calculated - meter_current
            difference_pct = difference / abs(meter_current) * 100.0
            channel_passed = abs(difference_pct) <= config["verification_tolerance_percent"]
            phase_result[current_key] = calculated
            phase_result[current_key + "_difference_a"] = difference
            phase_result[current_key + "_difference_pct"] = difference_pct
            phase_result[current_key + "_status"] = "PASS" if channel_passed else "FAIL"
            passed = passed and channel_passed
            print("  %s = %.6f А, отклонение %+.6f А (%+.2f%%): %s" % (
                current_key, calculated, difference, difference_pct, phase_result[current_key + "_status"]))
            phase_result["status"] = "PASS" if passed else "FAIL"
            verification["duty_%d" % duty] = {"A": phase_result}
        finally:
            stop_dc_test(console)
    phase_statuses = [phase_result["status"] for duty_result in verification.values() for phase_result in duty_result.values()]
    verification["overall_status"] = "PASS" if all(status == "PASS" for status in phase_statuses) else "FAIL"
    print("Общий статус проверки:", verification["overall_status"])
    return verification


def measure_phase_resistance(console, phase, config):
    duty = config["resistance_duty"]
    bus_voltage = read_bus_voltage("Введите измеренное Vbus для фазы %s перед DC-тестом [В]: " % phase)
    wait_for_ready("Подключите двигатель для фазы %s и нажмите Enter для запуска duty %d: " % (phase, duty))
    console.request("dc %s %d" % (phase, duty), "DC phase=", timeout=2.0)
    started_at = time.monotonic()
    try:
        wait_for_ready("DC-тест активен. Подождите стабилизации тока, затем нажмите Enter: ")
        if time.monotonic() - started_at > MAX_INTERACTIVE_DC_SECONDS:
            raise TimeoutError("Время ожидания ввода DC истекло; сторожевой таймер прошивки мог остановить тест")
        currents = parse_fields(console.request("i", "I,", timeout=2.0))
        current_keys = ("i%s1" % phase.lower(), "i%s2" % phase.lower())
        if any(key not in currents for key in current_keys):
            raise ValueError("Неполный ответ о токах для фазы %s" % phase)
        sensor_currents = [abs(currents[key]) for key in current_keys]
        current = statistics.fmean(sensor_currents)
        if current <= 0.05:
            raise ValueError("Ток для фазы %s слишком мал для измерения сопротивления" % phase)
        applied_voltage = applied_line_voltage(bus_voltage, duty, config["pwm_period"])
        return {"phase": phase, "duty": duty, "vbus_v": bus_voltage,
                "applied_voltage_v": applied_voltage, "sensor_currents_a": dict(zip(current_keys, sensor_currents)),
                "current_average_a": current, "phase_resistance_ohm": applied_voltage / current}
    finally:
        stop_dc_test(console)


def save_resistance_reports(console, config, session_id, results, winding_resistances, uart_log_path):
    report_dir = Path(config["output_dir"])
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": session_id, "com_port": console.port, "config": config,
               "uart_log": str(uart_log_path), "phase_measurements": results,
               "phase_resistances_ohm": winding_resistances}
    text_path = report_dir / ("resistance_%s.txt" % session_id)
    json_path = report_dir / ("resistance_%s.json" % session_id)
    csv_path = report_dir / ("resistance_%s.csv" % session_id)
    lines = ["STEVAL-IPM20B winding resistance report", "Timestamp: %s" % session_id,
             "COM port: %s" % console.port, "UART log: %s" % uart_log_path, "", "Phase measurements:"]
    add_mapping(lines, results)
    lines.extend(["", "Phase resistances [ohm]:"])
    add_mapping(lines, winding_resistances)
    text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=["record_type", "phase", "duty", "vbus_v", "applied_voltage_v", "current_a", "resistance_ohm"])
        writer.writeheader()
        for phase, result in results.items():
            writer.writerow({"record_type": "phase_measurement", "phase": phase, "duty": result["duty"],
                             "vbus_v": result["vbus_v"], "applied_voltage_v": result["applied_voltage_v"],
                             "current_a": result["current_average_a"], "resistance_ohm": result["phase_resistance_ohm"]})
        for phase, resistance in winding_resistances.items():
            writer.writerow({"record_type": "phase_resistance", "phase": phase, "resistance_ohm": resistance})
    return {"text": text_path, "json": json_path, "csv": csv_path}


def measure_winding_resistances(console, config, session_id, uart_log_path):
    print("Тест сопротивления использует откалиброванные коэффициенты тока и введённое вами значение Vbus.")
    stop_continuous_adc(console)
    results = {phase: measure_phase_resistance(console, phase, config) for phase in RESISTANCE_PHASES}
    winding_resistances = {phase: result["phase_resistance_ohm"] for phase, result in results.items()}
    print("\nСопротивления обмоток [Ом]:")
    for phase, resistance in winding_resistances.items():
        print("  R%s = %.6f" % (phase, resistance))
    report_paths = save_resistance_reports(console, config, session_id, results, winding_resistances, uart_log_path)
    print("Отчёты о сопротивлениях сохранены:", ", ".join(str(path) for path in report_paths.values()))


def add_mapping(lines, values, indent="  "): 
    if isinstance(values, list):
        for item in values:
            add_mapping(lines, item, indent)
        return
    if isinstance(values, dict):
        for key, value in values.items():
            if isinstance(value, dict):
                lines.append(indent + key + ":")
                add_mapping(lines, value, indent + "  ")
            elif isinstance(value, list):
                lines.append(indent + key + ":")
                add_mapping(lines, value, indent + "  ")
            elif isinstance(value, float):
                lines.append("%s%s = %.6f" % (indent, key, value))
            else:
                lines.append("%s%s = %s" % (indent, key, value))
    else:
        lines.append(indent + str(values))


def save_reports(console, config, session_id, offsets, zero_noise, measurements, scales, fit_quality, applied, verification, uart_log_path):
    report_dir = Path(config["output_dir"])
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {"timestamp": session_id, "com_port": console.port, "config": config,
               "uart_log": str(uart_log_path), "offsets": offsets, "zero_noise_mad_lsb": zero_noise,
               "calibration_measurements": measurements, "scale_factors_a_per_lsb": scales,
               "linearity": fit_quality, "scale_factors_applied": applied, "verification": verification}
    text_path = report_dir / ("calibration_%s.txt" % session_id)
    json_path = report_dir / ("calibration_%s.json" % session_id)
    csv_path = report_dir / ("calibration_%s.csv" % session_id)
    lines = ["STEVAL-IPM20B manual calibration report", "Timestamp: %s" % session_id,
             "COM port: %s" % console.port, "UART log: %s" % uart_log_path, "", "Configuration:"]
    add_mapping(lines, config)
    for title, values in (("Offsets:", offsets), ("Zero-current MAD noise [LSB]:", zero_noise),
                          ("Calibration measurements:", measurements), ("Scale factors [A/LSB]:", scales),
                          ("Linearity:", fit_quality), ("Verification:", verification or {"status": "NOT_PERFORMED"})):
        lines.extend(["", title])
        add_mapping(lines, values)
    text_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=["record_type", "duty", "phase", "channel", "value_a", "status", "residual_pct", "difference_pct"])
        writer.writeheader()
        for channel, scale in scales.items():
            quality = fit_quality[channel]
            writer.writerow({"record_type": "scale", "channel": channel, "value_a": scale,
                             "status": quality["status"], "residual_pct": quality["max_residual_pct"]})
        if verification:
            for duty_key, duty_result in verification.items():
                if duty_key == "overall_status":
                    continue
                for phase, phase_result in duty_result.items():
                    for adc_index in (1, 2):
                        channel = "i%s%d" % (phase.lower(), adc_index)
                        writer.writerow({"record_type": "verification", "duty": duty_key, "phase": phase,
                                         "channel": channel, "value_a": phase_result[channel],
                                         "status": phase_result[channel + "_status"],
                                         "difference_pct": phase_result[channel + "_difference_pct"]})
    return {"text": text_path, "json": json_path, "csv": csv_path}


def guided_calibration(console, config, session_id, uart_log_path):
    print("\nПроцедура калибрует положительные и отрицательные токи только в фазе A; коэффициенты применяются ко всем фазам. Введите abort в любом запросе для остановки.")
    stop_continuous_adc(console)
    wait_for_ready("Убедитесь, что ток двигателя равен нулю, затем нажмите Enter для калибровки смещений: ")
    offsets = parse_fields(console.request("calib", "CALIB DONE:", timeout=3.0))
    _, zero_noise = summarize_samples(raw_samples(console, config["sample_count"]))
    measurements = {}
    for duty in config["calibration_duties"]:
        print("\nТочка калибровки: duty %d" % duty)
        measurements["duty_%d" % duty] = {"A": measure_phase_for_calibration(console, "A", duty, config)}
    scales = {}
    fit_quality = {}
    points = [measurement_set["A"] for measurement_set in measurements.values()]
    for adc_index, channel in enumerate(PHASE_CHANNELS["A"], start=1):
        command = "scale%dA" % adc_index
        scales[command], fit_quality[command] = calculate_scale(points, offsets, zero_noise, channel, config)
    # Копируем коэффициенты фазы A на фазы B и C.
    for adc_index in (1, 2):
        a_command = "scale%dA" % adc_index
        for phase in ("B", "C"):
            command = "scale%d%s" % (adc_index, phase)
            scales[command] = scales[a_command]
            fit_quality[command] = fit_quality[a_command].copy()
    print("\nРассчитанные коэффициенты масштабирования [А/LSB]:")
    for command, value in scales.items():
        print("  %-7s %.8f, линейность %s (макс. остаток %.2f%%)" % (
            command, value, fit_quality[command]["status"], fit_quality[command]["max_residual_pct"]))
    applied = input("Введите apply для отправки этих значений в контроллер: ").strip().lower() == "apply"
    verification = None
    if applied:
        apply_scale_factors(console, scales)
        print("Значения калибровки применены. Начинается независимая проверка.")
        verification = verify_scale_factors(console, config)
    else:
        print("Значения не были применены.")
    report_paths = save_reports(console, config, session_id, offsets, zero_noise, measurements, scales, fit_quality, applied, verification, uart_log_path)
    print("Отчёты сохранены:", ", ".join(str(path) for path in report_paths.values()))


def parse_duties(value):
    duties = [int(item.strip()) for item in value.split(",") if item.strip()]
    if not duties or any(duty == 0 for duty in duties):
        raise argparse.ArgumentTypeError("duties должны содержать ненулевые целые числа через запятую")
    return duties


def build_config(args):
    config = DEFAULT_CONFIG.copy()
    if args.config:
        with args.config.open(encoding="utf-8") as config_file:
            file_config = json.load(config_file)
        if not isinstance(file_config, dict):
            raise ValueError("файл конфигурации должен содержать JSON-объект")
    overrides = {
        "calibration_duties": args.calibration_duties,
        "verification_duties": args.verification_duties,
        "sample_count": args.sample_count,
        "min_adc_delta_lsb": args.min_adc_delta_lsb,
        "noise_multiplier": args.noise_multiplier,
        "verification_tolerance_percent": args.verification_tolerance_percent,
        "linearity_tolerance_percent": args.linearity_tolerance_percent,
        "resistance_duty": args.resistance_duty,
        "pwm_period": args.pwm_period,
        "output_dir": args.output_dir,
    }
    config.update({key: value for key, value in overrides.items() if value is not None})
    config["calibration_duties"] = [int(duty) for duty in config["calibration_duties"]]
    config["verification_duties"] = [int(duty) for duty in config["verification_duties"]]
    if config["sample_count"] < 2:
        raise ValueError("sample_count должен быть не меньше 2")
    return config


def main(port, config):
    report_dir = Path(config["output_dir"])
    report_dir.mkdir(parents=True, exist_ok=True)
    session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    uart_log_path = report_dir / ("uart_%s.log" % session_id)
    console = SerialConsole(port, uart_log_path)
    print("Подключено к", port)
    print("Журнал UART:", uart_log_path)
    print("Введите calibrate для управляемого процесса с мультиметром, или q для выхода.\n")

    try:
        while True:
            command = input("> ").strip()
            if not command:
                continue
            if command.lower() in {"q", "quit", "exit"}:
                break
            if command.lower() == "calibrate":
                try:
                    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
                    guided_calibration(console, config, run_id, uart_log_path)
                except CalibrationAborted:
                    console.send("stop")
                    print("Калибровка прервана; DC-тест остановлен.")
                except (TimeoutError, ValueError, KeyError) as error:
                    console.send("stop")
                    print("Калибровка не удалась; DC-тест остановлен:", error)
                continue
            if command.lower() == "resistance":
                try:
                    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
                    measure_winding_resistances(console, config, run_id, uart_log_path)
                except CalibrationAborted:
                    console.send("stop")
                    print("Тест сопротивления прерван; DC-тест остановлен.")
                except (TimeoutError, ValueError, KeyError) as error:
                    console.send("stop")
                    print("Тест сопротивления не удался; DC-тест остановлен:", error)
                continue
            console.send(command)
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        console.close()
        print("Закрыто")


def request_port():
    ports = [port.device for port in serial.tools.list_ports.comports()]
    if ports:
        print("Доступные порты:", ", ".join(ports))
    while True:
        value = input("Введите номер COM-порта (например, 12) или полное имя COM12: ").strip()
        if not value:
            print("COM-порт обязателен.")
            continue
        return "COM" + value if value.isdigit() else value.upper()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("port", nargs="?")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--calibration-duties", type=parse_duties)
    parser.add_argument("--verification-duties", type=parse_duties)
    parser.add_argument("--sample-count", type=int)
    parser.add_argument("--min-adc-delta-lsb", type=float)
    parser.add_argument("--noise-multiplier", type=float)
    parser.add_argument("--verification-tolerance-percent", type=float)
    parser.add_argument("--linearity-tolerance-percent", type=float)
    parser.add_argument("--resistance-duty", type=int)
    parser.add_argument("--pwm-period", type=int)
    parser.add_argument("--output-dir")
    arguments = parser.parse_args()
    main(arguments.port or request_port(), build_config(arguments))
