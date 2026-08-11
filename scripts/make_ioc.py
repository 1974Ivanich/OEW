#!/usr/bin/env python3
"""
Адаптация валидного .ioc (3_Phase_Open_Claude) под OEW_Motor pinout.
Берём рабочий файл CubeMX как базу (гарантированно валидный формат) и
заменяем сигналы пинов + чистим лишнее. Нам нужен только csv pinout,
поэтому лишняя периферия (ADC1, DMA) удаляется.
"""
import os
import re
import sys

BASE = r"C:\ST\boyler\3_Phase_Open_Claude\3_Phase_Open_Claude.ioc"
OUT = r"C:\ST\boyler\Motor\OEW_Motor.ioc"

# Первый запуск берёт валидный .ioc из старого проекта как базу (формат CubeMX).
# На других машинах (или после первой генерации) база = текущий OEW_Motor.ioc:
# он уже валиден и содержит все наши поля — фильтры DROP_PREFIXES/EXTRA_IP
# пересобирают его детерминированно, поэтому идемпотентность сохраняется.
if not os.path.exists(BASE):
    if os.path.exists(OUT):
        BASE = OUT
        print("info: внешняя база не найдена, использую текущий OEW_Motor.ioc")
    else:
        print(f"FAIL: нет базы ({BASE}) и нет текущего .ioc — нечего адаптировать")
        sys.exit(1)

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
    "PA15": ("TIM2_CH1",  "Input_Capture", None),
}

# ── Дополнительные IP-конфигурации (не из PINS, добавляются в конец) ──────
# Значения TIM1/TIM8/ADC2/RCC = РЕАЛЬНЫЕ из src/pwm.c, src/adc.c, main.c:
#   PSC=16, ARR=999 (5кГц @10МГц), DT=1500нс, CMS=11 (mode3), RCR=1,
#   TRGO=Update; ADC2 injected (каналы 1,2,3,5); PLL 170МГц (M=4,N=85,R=2).
EXTRA_IP = """TIM1.AutoReloadPreload=TIM_AUTORELOAD_PRELOAD_ENABLE
TIM1.AutomaticOutput=TIM_AUTOMATICOUTPUT_ENABLE
TIM1.Channel-PWM\\ Generation1\\ CH1\\ CH1N=TIM_CHANNEL_1
TIM1.Channel-PWM\\ Generation2\\ CH2\\ CH2N=TIM_CHANNEL_2
TIM1.Channel-PWM\\ Generation3\\ CH3\\ CH3N=TIM_CHANNEL_3
TIM1.CounterMode=TIM_COUNTERMODE_CENTERALIGNED3
TIM1.DeadTime=1500
TIM1.IPParameters=Channel-PWM Generation1 CH1 CH1N,Channel-PWM Generation2 CH2 CH2N,Channel-PWM Generation3 CH3 CH3N,CounterMode,Period,AutoReloadPreload,TIM_MasterSlaveMode,TIM_MasterOutputTrigger,TIM_MasterOutputTrigger2,AutomaticOutput,DeadTime,RepetitionCounter,Prescaler
TIM1.Period=999
TIM1.Prescaler=16
TIM1.RepetitionCounter=1
TIM1.TIM_MasterOutputTrigger=TIM_TRGO_UPDATE
TIM1.TIM_MasterOutputTrigger2=TIM_TRGO2_UPDATE
TIM1.TIM_MasterSlaveMode=TIM_MASTERSLAVEMODE_ENABLE
TIM2.Channel-Input\\ Capture\\ Direct\\ Mode=TIM_CHANNEL_1
TIM2.IPParameters=Channel-Input Capture Direct Mode,Prescaler
TIM2.Prescaler=169
TIM8.AutoReloadPreload=TIM_AUTORELOAD_PRELOAD_ENABLE
TIM8.AutomaticOutput=TIM_AUTOMATICOUTPUT_ENABLE
TIM8.Channel-PWM\\ Generation1\\ CH1\\ CH1N=TIM_CHANNEL_1
TIM8.Channel-PWM\\ Generation2\\ CH2\\ CH2N=TIM_CHANNEL_2
TIM8.Channel-PWM\\ Generation3\\ CH3\\ CH3N=TIM_CHANNEL_3
TIM8.CounterMode=TIM_COUNTERMODE_CENTERALIGNED3
TIM8.DeadTime=1500
TIM8.IPParameters=Channel-PWM Generation1 CH1 CH1N,Channel-PWM Generation2 CH2 CH2N,Channel-PWM Generation3 CH3 CH3N,CounterMode,Period,AutoReloadPreload,TIM_MasterSlaveMode,AutomaticOutput,DeadTime,RepetitionCounter,Prescaler
TIM8.Period=999
TIM8.Prescaler=16
TIM8.RepetitionCounter=1
TIM8.TIM_MasterSlaveMode=TIM_MASTERSLAVEMODE_DISABLE
RCC.PLLM=RCC_PLLM_DIV4
RCC.PLLN=85
RCC.PLLR=RCC_PLLR_DIV2
RCC.PLLRCLKFreq_Value=170000000
RCC.PLLSourceVirtual=RCC_PLLSOURCE_HSI
RCC.PWRFreq_Value=170000000
RCC.SYSCLKSource=RCC_SYSCLKSOURCE_PLLCLK
RCC.SysClockFreqValue=170000000
RCC.IPParameters=ADC12Freq_Value,ADC345Freq_Value,AHBFreq_Value,APB1Freq_Value,APB1TimFreq_Value,APB2Freq_Value,APB2TimFreq_Value,CortexFreq_Value,FCLKCortexFreq_Value,HCLKFreq_Value,HSI_VALUE,PLLM,PLLN,PLLR,PLLRCLKFreq_Value,PLLSourceVirtual,PWRFreq_Value,SYSCLKSource,SysClockFreqValue
ADC2.Channel-0\\#ChannelRegularConversion=ADC_CHANNEL_1
ADC2.Channel-1\\#ChannelRegularConversion=ADC_CHANNEL_2
ADC2.Channel-2\\#ChannelRegularConversion=ADC_CHANNEL_3
ADC2.Channel-3\\#ChannelRegularConversion=ADC_CHANNEL_5
ADC2.CommonPathInternal=null|null|null|null
ADC2.EOCSelection=ADC_EOC_SEQ_CONV
ADC2.ExternalTrigConv=ADC_EXTERNALTRIG_T1_TRGO
ADC2.IPParameters=Rank-0\\#ChannelRegularConversion,Channel-0\\#ChannelRegularConversion,SamplingTime-0\\#ChannelRegularConversion,OffsetNumber-0\\#ChannelRegularConversion,NbrOfConversionFlag,Rank-1\\#ChannelRegularConversion,Channel-1\\#ChannelRegularConversion,SamplingTime-1\\#ChannelRegularConversion,OffsetNumber-1\\#ChannelRegularConversion,Rank-2\\#ChannelRegularConversion,Channel-2\\#ChannelRegularConversion,SamplingTime-2\\#ChannelRegularConversion,OffsetNumber-2\\#ChannelRegularConversion,Rank-3\\#ChannelRegularConversion,Channel-3\\#ChannelRegularConversion,SamplingTime-3\\#ChannelRegularConversion,OffsetNumber-3\\#ChannelRegularConversion,NbrOfConversion,ExternalTrigConv,EOCSelection,CommonPathInternal
ADC2.NbrOfConversion=4
ADC2.NbrOfConversionFlag=1
ADC2.OffsetNumber-0\\#ChannelRegularConversion=ADC_OFFSET_NONE
ADC2.OffsetNumber-1\\#ChannelRegularConversion=ADC_OFFSET_NONE
ADC2.OffsetNumber-2\\#ChannelRegularConversion=ADC_OFFSET_NONE
ADC2.OffsetNumber-3\\#ChannelRegularConversion=ADC_OFFSET_NONE
ADC2.Rank-0\\#ChannelRegularConversion=1
ADC2.Rank-1\\#ChannelRegularConversion=2
ADC2.Rank-2\\#ChannelRegularConversion=3
ADC2.Rank-3\\#ChannelRegularConversion=4
ADC2.SamplingTime-0\\#ChannelRegularConversion=ADC_SAMPLETIME_2CYCLES_5
ADC2.SamplingTime-1\\#ChannelRegularConversion=ADC_SAMPLETIME_2CYCLES_5
ADC2.SamplingTime-2\\#ChannelRegularConversion=ADC_SAMPLETIME_2CYCLES_5
ADC2.SamplingTime-3\\#ChannelRegularConversion=ADC_SAMPLETIME_2CYCLES_5
"""

# ── Удаляем лишние строки (пины не из нашего списка + DMA + ADC1) ─────────
DROP_PREFIXES = (
    "PA5.", "PA8.", "PA13.", "PA14.", "PB3.", "PB13.", "PB14.", "PB15.",
    "PC13", "PC14", "PC15", "PF0", "PF1",
    "Dma.", "Dma", "ADC1.", "SH.ADC1",
    "NUCLEO-G474RE.", "VP_CORDIC", "VP_TIM1", "VP_TIM8", "VP_NUCLEO",
    # ADC2/RCC полностью контролируются из EXTRA_IP — старые значения из базы
    # (старый проект: каналы 2,3,4, PLLM_DIV6, HSE) выкидываем
    "ADC2.", "RCC.",
)

keep = []
for ln in lines:
    if any(ln.startswith(p) for p in DROP_PREFIXES):
        continue
    # старые TIM1./TIM8./TIM2. конфигурации удаляем — новые из EXTRA_IP
    if ln.startswith("TIM1.") or ln.startswith("TIM8.") or ln.startswith("TIM2."):
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
    if ln.startswith("Mcu.ThirdPartyNb"):
        continue  # перегенерируем ниже (new_mcu)
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

# ── Дописываем EXTRA_IP (TIM1/TIM2/TIM8) после NVIC-блока (если ещё нет) ──
if "TIM1.Channel-PWM" not in "".join(final):
    # вставляем после последней строки перед RCC (или после Mcu-блока)
    last_tim1 = 0
    for i, ln in enumerate(final):
        if ln.startswith("Mcu."):
            last_tim1 = i
    final = final[:last_tim1 + 1] + [EXTRA_IP] + final[last_tim1 + 1:]

with open(OUT, "w", encoding="utf-8") as f:
    f.writelines(final)

print(f"OK: {OUT} — {len(final)} строк")
