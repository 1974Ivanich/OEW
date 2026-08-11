#!/usr/bin/env python3
"""
cubemx_check.py — самоконтроль конфигурации периферии через STM32CubeMX.

Запускает CubeMX headless с эталонным OEW_Motor.ioc, выгружает CSV-распиновку
и сверяет её с ожидаемой конфигурацией (таблица ниже, актуализирована по
PROJECT_OVERVIEW.md / pinout.md).

Использование:
    python cubemx_check.py            # полный прогон (CubeMX headless + сверка)
    python cubemx_check.py --csv      # только сверка по готовому CSV (без CubeMX)

Коды выхода: 0 = PASS, 1 = FAIL (расхождение), 2 = ошибка прогона.
"""
import argparse
import csv
import os
import subprocess
import sys
import tempfile

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IOC_PATH = os.path.join(PROJECT_DIR, "OEW_Motor.ioc")
CUBEMX_EXE = r"C:\Program Files\STMicroelectronics\STM32Cube\STM32CubeMX\STM32CubeMX.exe"
DEFAULT_CSV = os.path.join(PROJECT_DIR, "logs", "cubemx_pinout.csv")

# ── Ожидаемая конфигурация (эталон самоконтроля) ──────────────────────────
# (pin, signal) — сигнал в нотации CubeMX CSV ("S_..." префикс не обязателен)
EXPECTED = {
    # TIM1 — инвертор 1 (HIN высокий ключ, LIN комплементарный)
    "PC0":  "TIM1_CH1",
    "PA7":  "TIM1_CH1N",
    "PC1":  "TIM1_CH2",
    "PB0":  "TIM1_CH2N",
    "PC2":  "TIM1_CH3",
    "PB1":  "TIM1_CH3N",
    # TIM8 — инвертор 2
    "PC6":  "TIM8_CH1",
    "PC10": "TIM8_CH1N",
    "PC7":  "TIM8_CH2",
    "PC11": "TIM8_CH2N",
    "PC8":  "TIM8_CH3",
    "PC12": "TIM8_CH3N",
    # ADC2 — токи и напряжение
    "PA0":  "ADC2_IN1",   # I1 (фазный U)
    "PA1":  "ADC2_IN2",   # I2 (фазный V)
    "PA6":  "ADC2_IN3",   # Ires (DC-link)
    "PC4":  "ADC2_IN5",   # VBUS (делитель 1:125)
    # USART2 — связь
    "PA2":  "USART2_TX",
    "PA3":  "USART2_RX",
    # Управление
    "PB4":  "GPIO_Output",  # EN1
    "PB5":  "GPIO_Output",  # EN2
    "PB6":  "GPIO_Output",  # sync-триггер (D13)
    # Энкодер AS5048A (PWM input capture)
    "PA15": "TIM2_CH1",
}


def run_cubemx(csv_path: str) -> bool:
    """Запуск CubeMX headless: config load → csv pinout → exit."""
    script = os.path.join(PROJECT_DIR, "scripts", "cubemx_check_script.txt")
    with open(script, "w", encoding="utf-8") as f:
        f.write(f"config load {IOC_PATH}\r\n")
        f.write(f"csv pinout {csv_path}\r\n")
        f.write("exit\r\n")
    print(f"[cubemx] запуск: {CUBEMX_EXE} -q {script}")
    try:
        r = subprocess.run(
            [CUBEMX_EXE, "-q", script],
            capture_output=True, text=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        print("[cubemx] TIMEOUT (300 c)")
        return False
    out = (r.stdout or "") + (r.stderr or "")
    ok_load = "KO" not in out.split("csv pinout")[0] if "csv pinout" in out else False
    print("[cubemx] exit code:", r.returncode, "| load OK:", ok_load)
    return ok_load and os.path.exists(csv_path)


def parse_csv(csv_path: str) -> dict:
    """CSV CubeMX → {pin: signal}."""
    result = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = (row.get("Name") or "").strip()
            signal = (row.get("Signal") or "").strip()
            if name and signal:
                result[name] = signal
    return result


def check(csv_path: str) -> int:
    actual = parse_csv(csv_path)
    if not actual:
        print("[check] CSV пустой или не прочитан:", csv_path)
        return 2
    print(f"[check] пинов в CSV CubeMX: {len(actual)}")
    fails = []
    for pin, exp_signal in sorted(EXPECTED.items()):
        act_signal = actual.get(pin, "")
        # нормализация: убираем S_ префикс и пробелы
        act_norm = act_signal.replace("S_", "").strip()
        exp_norm = exp_signal.replace("S_", "").strip()
        if act_norm != exp_norm:
            fails.append(f"  {pin}: ожидалось {exp_signal!r}, в CubeMX {act_signal!r}")
    # пины, которые есть в CubeMX, но не ожидались (лишние)
    extra = sorted(set(actual) - set(EXPECTED))
    for pin in extra:
        fails.append(f"  {pin}: лишний пин в CubeMX ({actual[pin]!r})")
    if fails:
        print("[check] FAIL — расхождения:")
        for f in fails:
            print(f)
        return 1
    print(f"[check] PASS — все {len(EXPECTED)} пинов совпадают")
    return 0


def main():
    ap = argparse.ArgumentParser(description="CubeMX самоконтроль распиновки")
    ap.add_argument("--csv", help="использовать готовый CSV вместо запуска CubeMX")
    args = ap.parse_args()

    if args.csv:
        return check(args.csv)
    csv_path = DEFAULT_CSV
    if not run_cubemx(csv_path):
        print("[cubemx] прогон не удался — проверьте .ioc и CubeMX")
        return 2
    return check(csv_path)


if __name__ == "__main__":
    sys.exit(main())
