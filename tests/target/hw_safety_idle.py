"""HWT: состояние силовой части в покое — только чтение, без HV и без запуска.

Проверяется то, что оператор обычно смотрит глазами: MOE снят, таймеры
остановлены, признаков срабатывания break нет, конфигурация BDTR соответствует
инварианту PWM_Init (`src/pwm.c`). Сценарий ничего не меняет: под HV запускать
его не требуется, а инъекции запрещены.

Внимание: это НЕ замена измерениям. HWT читает цифровое состояние через
отладчик и не подтверждает ни форму сигналов, ни токи (DDTT §8).
"""

from __future__ import annotations

from hwt_stm32g4 import (PWM_BDTR_FORBIDDEN, PWM_BDTR_REQUIRED,
                         PWM_BREAK_STATUS_MASK, TIM1_BDTR, TIM1_CR1, TIM1_SR,
                         TIM8_BDTR, TIM8_CR1, TIM8_SR, TIM_BDTR_MOE, TIM_CR1_CEN)
from hwt_target import case

CHECKPOINT = "CLI_ProcessLine"

TIMERS = (("TIM1", TIM1_BDTR, TIM1_CR1, TIM1_SR),
          ("TIM8", TIM8_BDTR, TIM8_CR1, TIM8_SR))


@case("HW_SAFETY_IDLE", timeout_s=20, labels=("safety", "no-hv"),
      contracts=(CHECKPOINT, "fault", "fault_reason"),
      description="Покой: MOE снят, таймеры стоят, break не срабатывал, BDTR "
                  "соответствует инварианту PWM_Init")
def hw_safety_idle(t):
    t.reach(CHECKPOINT)

    for name, bdtr_addr, cr1_addr, sr_addr in TIMERS:
        bdtr = t.read_u32(bdtr_addr)
        t.check(f"{name} MOE снят", bdtr & TIM_BDTR_MOE, 0,
                detail=f"BDTR=0x{bdtr:08X} (MOE — бит 15)")
        t.check(f"{name} BDTR без запрещённых бит",
                bdtr & PWM_BDTR_FORBIDDEN, 0,
                detail="BKP/BK2E/AOE — src/pwm.c PWM_BDTR_FORBIDDEN")
        t.check(f"{name} BDTR с обязательными битами",
                bdtr & PWM_BDTR_REQUIRED, PWM_BDTR_REQUIRED,
                detail="BKE/OSSR/OSSI — src/pwm.c PWM_BDTR_REQUIRED")

        cr1 = t.read_u32(cr1_addr)
        t.check(f"{name} счёт остановлен", cr1 & TIM_CR1_CEN, 0,
                detail=f"CR1=0x{cr1:08X} (CEN — бит 0)")

        # Признак срабатывания break — в TIMx_SR (BIF/B2IF), не в BDTR:
        # src/pwm.c PWM_BREAK_STATUS_MASK = TIM_SR_BIF | TIM_SR_B2IF.
        sr = t.read_u32(sr_addr)
        t.check(f"{name} break не срабатывал", sr & PWM_BREAK_STATUS_MASK, 0,
                detail=f"SR=0x{sr:08X}, маска 0x{PWM_BREAK_STATUS_MASK:08X}")

    t.check("PROTECT fault не латчен", t.word_at_symbol("fault"), 0)
    t.check("Причина фолта пуста", t.word_at_symbol("fault_reason"), 0)
