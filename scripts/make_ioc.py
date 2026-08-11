#!/usr/bin/env python3
"""
Адаптация валидного .ioc (3_Phase_Open_Claude) под OEW_Motor pinout.
Берём рабочий файл CubeMX как базу (гарантированно валидный формат) и
заменяем сигналы пинов + чистим лишнее. Нам нужен только csv pinout,
поэтому лишняя периферия (ADC1, DMA) удаляется.
"""
import re
import sys

BASE = r"C:\ST\boyler\3_Phase_Open_Claude\3_Phase_Open_Claude.ioc"
OUT = r"C:\ST\boyler\Motor\OEW_Motor.ioc"

with open(BASE, encoding="utf-8") as f:
    lines = f.readlines()

# ── Целевая распиновка: pin -> (Signal, Mode, Label?) ─────────────────────
PINS = {
    # TIM1 — Inv1 (HIN/LIN)
    "PC0":  ("TIM1_CH1",    "PWM Generation1 CH1 CH1N", None),
    "PA7":  ("TIM1_CH1N",   "PWM Generation1 CH1 CH1N", None),
    "PC1":  ("TIM1_CH2",    "PWM Generation2 CH2 CH2N", None),
    "PB0":  ("TIM1_CH2N",   "PWM Generation2 CH2 CH2N", None),
    "PC2":  ("TIM1_CH3",    "PWM Generation3 CH3 CH3N", None),
    "PB1":  ("TIM1_CH3N",   "PWM Generation3 CH3 CH3N", None),
    # TIM8 — Inv2
    "PC6":  ("TIM8_CH1",    "PWM Generation1 CH1 CH1N", None),
    "PC10": ("TIM8_CH1N",   "PWM Generation1 CH1 CH1N", None),
    "PC7":  ("TIM8_CH2",    "PWM Generation2 CH2 CH2N", None),
    "PC11": ("TIM8_CH2N",   "PWM Generation2 CH2 CH2N", None),
    "PC8":  ("TIM8_CH3",    "PWM Generation3 CH3 CH3N", None),
    "PC12": ("TIM8_CH3N",   "PWM Generation3 CH3 CH3N", None),
    # ADC2 — токи/напряжение
    "PA0":  ("ADC2_IN1",    "IN1-Single-Ended", None),
    "PA1":  ("ADC2_IN2",    "IN2-Single-Ended", None),
    "PA6":  ("ADC2_IN3",    "IN3-Single-Ended", None),
    "PC4":  ("ADC2_IN5",    "IN5-Single-Ended", None),
    # USART2
    "PA2":  ("USART2_TX",   "Asynchronous", None),
    "PA3":  ("USART2_RX",   "Asynchronous", None),
    # GPIO: EN1/EN2/TRIG
    "PB4":  ("GPIO_Output", "GPIO_Output", "EN1"),
    "PB5":  ("GPIO_Output", "GPIO_Output", "EN2"),
    "PB6":  ("GPIO_Output", "GPIO_Output", "TRIG"),
    # Энкодер
    "PA15": ("S_TIM2_CH1",  "Input_Capture", None),
}

# ── Удаляем лишние строки (пины не из нашего списка + DMA + ADC1) ─────────
DROP_PREFIXES = (
    "PA5.", "PA8.", "PA13.", "PA14.", "PB3.", "PB13.", "PB14.", "PB15.",
    "PC13", "PC14", "PC15", "PF0", "PF1",
    "Dma.", "Dma", "ADC1.", "SH.ADC1",
    "NUCLEO-G474RE.", "VP_CORDIC", "VP_TIM1", "VP_TIM8", "VP_NUCLEO",
)

keep = []
for ln in lines:
    if any(ln.startswith(p) for p in DROP_PREFIXES):
        continue
    # сигналы пинов заменяем ниже; здесь просто пропускаем старые блоки пинов
    pin_match = re.match(r"^(PA\d+|PB\d+|PC\d+)[.\[]", ln)
    if pin_match:
        pin = pin_match.group(1)
        if pin not in PINS:
            continue  # лишний пин — выкидываем
        # старые строки пина выкидываем, новые добавим ниже
        continue
    keep.append(ln)

def pin_key(p):
    m = re.match(r"([A-Z]+)(\d+)", p)
    return (m.group(1), int(m.group(2)))

# ── Генерируем новые блоки пинов ───────────────────────────────────────────
pin_blocks = []
for pin in sorted(PINS, key=pin_key):
    sig, mode, label = PINS[pin]
    pin_blocks.append(f"{pin}.Signal={sig}\n")
    if mode:
        pin_blocks.append(f"{pin}.Mode={mode}\n")
    if label:
        pin_blocks.append(f"{pin}.GPIOParameters=GPIO_Label\n")
        pin_blocks.append(f"{pin}.GPIO_Label={label}\n")
        pin_blocks.append(f"{pin}.Locked=true\n")

# ── Обновляем Mcu.IP / Mcu.Pin списки ─────────────────────────────────────
out_lines = []
pin_idx = 0
for ln in keep:
    if ln.startswith("Mcu.Pin"):
        continue  # перегенерируем ниже
    if ln.startswith("Mcu.IP"):
        continue  # перегенерируем ниже
    if ln.startswith("Mcu.IPNb"):
        continue
    if ln.startswith("Mcu.PinsNb"):
        continue
    out_lines.append(ln)

# вставляем новые списки после Mcu.CPN
mcu_pins = []
mcu_ips = ["ADC2", "CORDIC", "NVIC", "RCC", "SYS", "TIM1", "TIM2", "TIM8", "USART2"]
for i, p in enumerate(sorted(PINS, key=pin_key)):
    mcu_pins.append(f"Mcu.Pin{i}={p}\n")
mcu_pins.append(f"Mcu.Pin{len(mcu_pins)}=VP_SYS_VS_Systick\n")

insert_at = None
for i, ln in enumerate(out_lines):
    if ln.startswith("Mcu.CPN"):
        insert_at = i + 1
        break

if insert_at is None:
    print("FAIL: Mcu.CPN не найден")
    sys.exit(1)

head = out_lines[:insert_at]
tail = out_lines[insert_at:]
new_mcu = []
for i, ip in enumerate(mcu_ips):
    new_mcu.append(f"Mcu.IP{i}={ip}\n")
new_mcu.append(f"Mcu.IPNb={len(mcu_ips)}\n")
new_mcu.extend(mcu_pins)
new_mcu.append(f"Mcu.PinsNb={len(mcu_pins)}\n")
new_mcu.append("Mcu.ThirdPartyNb=0\n")

final = head + new_mcu + pin_blocks + tail

with open(OUT, "w", encoding="utf-8") as f:
    f.writelines(final)

print(f"OK: {OUT} — {len(final)} строк")
