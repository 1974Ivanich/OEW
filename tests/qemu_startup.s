/* Минимальный startup для QEMU-теста (без libc, без startup_stm32g474xx.s).
 * Векторная таблица: SP + Reset_Handler → main.
 * QEMU -kernel загружает ELF по адресу загрузки; с -Ttext=0x08000000
 * векторная таблица должна лежать по 0x08000000. */
    .syntax unified
    .cpu cortex-m4
    .thumb

    .section .isr_vector, "a"
    .align 2
    .globl _estack
    .globl Reset_Handler
    .globl Default_Handler

_estack:
    .word 0x20020000          /* вершина стека (RAM STM32F405: 128KB @ 0x20000000) */
    .word Reset_Handler       /* reset */
    .word Default_Handler     /* NMI */
    .word Default_Handler     /* HardFault */
    .word Default_Handler     /* MemManage */
    .word Default_Handler     /* BusFault */
    .word Default_Handler     /* UsageFault */
    .word 0, 0, 0, 0          /* reserved */
    .word Default_Handler     /* SVCall */
    .word 0, 0               /* DebugMon, reserved */
    .word Default_Handler     /* PendSV */
    .word Default_Handler     /* SysTick */
    /* остальные — Default_Handler */
    .rept 80
    .word Default_Handler
    .endr

    .text
    .thumb_func
    .globl Reset_Handler
Reset_Handler:
    ldr sp, =0x20020000      /* вершина стека RAM (128KB @ 0x20000000) */
    bl main
1:  b 1b                    /* main вернулся — зациклиться */

    .thumb_func
    .globl Default_Handler
Default_Handler:
    b .
