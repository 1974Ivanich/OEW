"""HWT: конфигурация входа аварийного торможения (BKIN) — только чтение.

Проверка 3A протокола Record A: BKINE/BKINP в TIMx_AF1 и BKE/BKF в TIMx_BDTR
должны соответствовать тому, что ставит PWM_Init (src/pwm.c:278-283, 300-301) и
что проверяет собственная функция прошивки timer_break_configured()
(src/pwm.c:154-169). Регистры читаются по абсолютному адресу: символических
выражений (`TIM1->AF1`) транспорт не поддерживает (DDTT: предел RSP).

Ожидание зависит от сборки, и это не подгонка под наблюдение: в прошивке два
штатных режима (src/pwm.c:253-257) — production (`pwm_break_bits = BKE|BKF`) и
monitor-only (`pwm_break_bits = 0`, «SD — только входы наблюдения»). Режим
определяется по самим регистрам, каждый проверяет свой инвариант; неполная
конфигурация (BKE без BKF или наоборот) — FAIL в любом режиме.

AF1 в этом заголовке CMSIS имён бит не экспортирует, поэтому биты — литералы по
RM0440, как в src/pwm.c:39-43.
"""

from __future__ import annotations

from hwt_stm32g4 import (PWM_BDTR_FORBIDDEN, PWM_BDTR_REQUIRED,
                         PWM_BREAK_STATUS_MASK, TIM1_AF1, TIM1_BDTR, TIM1_SR,
                         TIM8_AF1, TIM8_BDTR, TIM8_SR, TIM_AF1_BKINE,
                         TIM_AF1_BKINP, TIM_BDTR_BKE, TIM_BDTR_BKF)
from hwt_target import case

# Контрольная точка — измеренно исполняемый символ. CLI_ProcessLine в этой
# сборке (-Os) не исполняется: точка по нему не срабатывает никогда
# (проверено зондом по живому: 0x080036C8 внутри UART_ReadLine → T05 сразу,
# CLI_ProcessLine 0x0800B544 → тишина; вызов инлайнится в вызывающего).
CHECKPOINT = "UART_ReadLine"

TIMERS = (("TIM1", TIM1_AF1, TIM1_BDTR, TIM1_SR),
          ("TIM8", TIM8_AF1, TIM8_BDTR, TIM8_SR))
BREAK_BITS = TIM_BDTR_BKE | TIM_BDTR_BKF


@case("HW_BKIN_CONFIG", timeout_s=20, labels=("safety", "no-hv"),
      contracts=(CHECKPOINT,),
      description="BKIN: AF1 BKINE/BKINP и BDTR BKE/BKF по PWM_Init и "
                  "timer_break_configured() из src/pwm.c")
def hw_bkin_config(t):
    t.reach(CHECKPOINT)

    for name, af1_addr, bdtr_addr, sr_addr in TIMERS:
        af1 = t.read_u32(af1_addr)
        t.check(f"{name} AF1.BKINE включён (внешний BKIN)", af1 & TIM_AF1_BKINE,
                TIM_AF1_BKINE,
                detail=f"AF1=0x{af1:08X}; BKINE — бит 0 (RM0440, src/pwm.c:42)")
        t.check(f"{name} AF1.BKINP выключен (активный низкий)",
                af1 & TIM_AF1_BKINP, 0,
                detail=f"AF1=0x{af1:08X}; BKINP — бит 9 (RM0440, src/pwm.c:43)")

        bdtr = t.read_u32(bdtr_addr)
        t.check(f"{name} BDTR без запрещённых бит", bdtr & PWM_BDTR_FORBIDDEN, 0,
                detail="BKP/BK2E/AOE — src/pwm.c:38 PWM_BDTR_FORBIDDEN")

        break_bits = bdtr & BREAK_BITS
        if break_bits == 0:
            t.check(f"{name} BDTR: сборка monitor-only (BKE=0, BKF=0)", 0, 0,
                    detail="src/pwm.c:253-257 — pwm_break_bits = 0, SD только "
                           "входы наблюдения; проверять нечего, это штатный режим")
        else:
            t.check(f"{name} BDTR: BKE и BKF выставлены (production)",
                    break_bits, BREAK_BITS,
                    detail=f"BDTR=0x{bdtr:08X}; BKE — бит 12, MOE — бит 15, "
                           "BKF[3:0] — биты 19..16 (RM0440; src/pwm.c:37, 256)")
            t.check(f"{name} BDTR: обязательные биты целиком",
                    bdtr & PWM_BDTR_REQUIRED, PWM_BDTR_REQUIRED,
                    detail="BKE/BKF/OSSR/OSSI — src/pwm.c:37")

        sr = t.read_u32(sr_addr)
        t.check(f"{name} break не срабатывал", sr & PWM_BREAK_STATUS_MASK, 0,
                detail=f"SR=0x{sr:08X}; BIF/B2IF — src/pwm.c:36, биты 7 и 8")
