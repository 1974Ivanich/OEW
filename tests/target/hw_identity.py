"""HWT: живая ли прошивка на плате и та ли это прошивка (no-HV, только чтение).

Проверки отвечают на два самых частых вопроса стенда:
* «плата не отвечает» — стоит ли ядро, идёт ли основной цикл, нет ли
  latched fault ядра;
* «команда unknown» — совпадает ли образ в Flash с тем, по которому собран
  сценарий (полная сверка CRC — встроенная проверка HWT_IMAGE у раннера).

Ничего не записывается и не инжектируется: сценарий только читает.
"""

from __future__ import annotations

from hwt_stm32g4 import (FLASH_BASE, FLASH_SIZE, RAM_BASE, RAM_SIZE,
                        SCB_CFSR, SCB_HFSR)
from hwt_target import case

# Точка наблюдения: обработчик CLI вызывается из основного цикла, то есть
# каждая остановка здесь — доказательство, что цикл жив (main.c).
CHECKPOINT = "CLI_ProcessLine"


@case("HW_ALIVE", timeout_s=20, labels=("boot", "identity"),
      contracts=(CHECKPOINT, "main", "fault"),
      description="Ядро на цели живо, код в Flash, latched fault ядра нет")
def hw_alive(t):
    t.reach(CHECKPOINT)

    pc = t.value("$pc")
    t.check("pc в диапазоне Flash", FLASH_BASE <= pc < FLASH_BASE + FLASH_SIZE,
            True, detail=f"pc=0x{pc:08X}")

    sp = t.value("$sp")
    t.check("sp в диапазоне RAM", RAM_BASE <= sp <= RAM_BASE + RAM_SIZE, True,
            detail=f"sp=0x{sp:08X}")

    # CFSR/HFSR: ненулевое значение = была ошибка ядра. Читаем регистр ядра,
    # а не «флаг в прошивке»: это состояние железа (core_cm4.h, RM0440 §4).
    t.check("SCB->CFSR без ошибок", t.read_u32(SCB_CFSR), 0,
            detail="BusFault/UsageFault/MemManage — не было")
    t.check("SCB->HFSR без ошибок", t.read_u32(SCB_HFSR), 0,
            detail="HardFault — не было")

    # Центральный латч: 0 = фолта нет (src/protect.c, static volatile int fault).
    t.check("PROTECT fault не латчен", t.word_at_symbol("fault"), 0)


@case("HW_LOOP_PROGRESS", timeout_s=25, labels=("liveness",),
      contracts=(CHECKPOINT,),
      description="Основной цикл идёт: точка CLI достигается повторно")
def hw_loop_progress(t):
    # Два прохода одной и той же точки: второй проход невозможен, если ядро
    # стоит в HardFault-е или залипло в ожидании.
    t.reach(CHECKPOINT, occurrence=2)
    t.check("цикл проходит точку CLI дважды", 2, 2)
