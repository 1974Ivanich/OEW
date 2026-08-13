#include "stm32g474xx.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include "uart.h"
#include "pwm.h"
#include "adc.h"
#include "foc.h"
#include "protect.h"
#include "autotune.h"
#include "cordic_math.h"
#include "encoder.h"
#include "vf_control.h"
#include "swo.h"
/* ── SWO-дублёр отладочных сообщений ────────────────────────────────────────
 * Меню/ошибки/статусы идут И в UART (GUI), И в SWO (отладчик).
 * Телеметрия (@FOC/@ADC/@PWM/@TRIG) и промпт "> " НЕ дублируются — это
 * GUI-протокол, SWO засоряется. */
#define DBG_STR(s)      do { UART_SendStr(s); SWO_SendStr(s); } while(0)
#define DBG_FMT(fmt, ...) do { UART_SendTelemetry(fmt, ##__VA_ARGS__); \
                                SWO_Printf(fmt, ##__VA_ARGS__); } while(0)


volatile uint32_t sys_tick_ms = 0;   /* внешняя линковка — используется encoder.c (ENC_Calibrate) */
void SysTick_Handler(void) { sys_tick_ms++; }

static void GPIO_Init(void) {
    RCC->AHB2ENR |= RCC_AHB2ENR_GPIOAEN | RCC_AHB2ENR_GPIOBEN | RCC_AHB2ENR_GPIOCEN;
    GPIOC->MODER &= ~((3U<<0)|(3U<<2)|(3U<<4));
    GPIOC->MODER |=  (2U<<0)|(2U<<2)|(2U<<4);
    GPIOC->OSPEEDR |= (3U<<0)|(3U<<2)|(3U<<4);
    GPIOC->AFR[0] &= ~((0xF<<0)|(0xF<<4)|(0xF<<8));
    GPIOC->AFR[0] |=  (2U<<0)|(2U<<4)|(2U<<8);
    GPIOA->MODER &= ~(3U<<14); GPIOA->MODER |= (2U<<14);
    GPIOA->OSPEEDR |= (3U<<14);
    GPIOA->AFR[0] &= ~(0xF<<28); GPIOA->AFR[0] |= (6U<<28);
    GPIOB->MODER &= ~((3U<<0)|(3U<<2)); GPIOB->MODER |= (2U<<0)|(2U<<2);
    GPIOB->OSPEEDR |= (3U<<0)|(3U<<2);
    GPIOB->AFR[0] &= ~((0xF<<0)|(0xF<<4)); GPIOB->AFR[0] |= (6U<<0)|(6U<<4);
    GPIOC->MODER &= ~((3U<<12)|(3U<<14)|(3U<<16));
    GPIOC->MODER |=  (2U<<12)|(2U<<14)|(2U<<16);
    GPIOC->OSPEEDR |= (3U<<12)|(3U<<14)|(3U<<16);
    GPIOC->AFR[0] &= ~((0xF<<24)|(0xF<<28));
    GPIOC->AFR[0] |=  (4U<<24)|(4U<<28);
    GPIOC->AFR[1] &= ~(0xF<<0); GPIOC->AFR[1] |= (4U<<0);
    GPIOC->MODER &= ~((3U<<20)|(3U<<22)|(3U<<24));
    GPIOC->MODER |=  (2U<<20)|(2U<<22)|(2U<<24);
    GPIOC->OSPEEDR |= (3U<<20)|(3U<<22)|(3U<<24);
    GPIOC->AFR[1] &= ~((0xF<<8)|(0xF<<12)|(0xF<<16));
    GPIOC->AFR[1] |=  (4U<<8)|(4U<<12)|(4U<<16);
    GPIOB->MODER &= ~((3U<<8)|(3U<<10)); GPIOB->MODER |= (1U<<8)|(1U<<10);
    GPIOB->OSPEEDR |= (1U<<8)|(1U<<10);
    GPIOB->PUPDR &= ~((3U<<8)|(3U<<10));  GPIOB->PUPDR |= (2U<<8)|(2U<<10);
    GPIOB->BSRR = (1U<<4)|(1U<<5);   /* EN1, EN2 = HIGH */
    GPIOA->MODER |= (3U<<0)|(3U<<2)|(3U<<12);
    GPIOC->MODER |= (3U<<8);
    /* PB6 — hardware sync trigger (освобождён после удаления SPI2 CS).
     * Push-pull output, LOW по умолчанию. Используется для точной
     * привязки UART-телеметрии (vflog) к захвату sigrok (см. TIM6 vflog
     * и команду "vf=" в главном цикле — TRIG_High()/TRIG_Low()). */
    GPIOB->MODER &= ~(3U<<12); GPIOB->MODER |= (1U<<12);
    GPIOB->OTYPER &= ~(1U<<6);
    GPIOB->OSPEEDR |= (3U<<12);
    GPIOB->BSRR = (1U<<(6+16));  /* PB6 = LOW */
}

static inline void TRIG_High(void) { GPIOB->BSRR = (1U<<6); }
static inline void TRIG_Low(void)  { GPIOB->BSRR = (1U<<(6+16)); }

void ADC1_2_IRQHandler(void) {
    uint32_t isr = ADC2->ISR;
    if(isr & ADC_ISR_OVR) {
        ADC2->ISR = ADC_ISR_OVR;
        extern volatile uint32_t adc_ovr_count;
        adc_ovr_count++;
    }
    if(isr & ADC_ISR_JEOS) {
        ADC2->ISR = ADC_ISR_JEOS;
        extern volatile uint32_t adc_jeos_count;
        adc_jeos_count++;
        ADC_ReadInjected();
        /* Guard: если PWM_Disable() уже остановил TIM1 (CEN=0), не вызываем
         * FOC_Run на остановленном PWM — pending JEOS от предыдущего цикла
         * может прийти после PWM_Disable(). FOC_Stop()/VFC_Stop() сбрасывают
         * флаг running, но между PWM_Disable() и FOC_Stop() в main остаётся
         * окно, где FOC_IsRunning() ещё true, а TIM1 уже остановлен. */
        if(FOC_IsRunning() && (TIM1->CR1 & TIM_CR1_CEN)) {
            PROTECT_Check();
            if(PROTECT_IsFault()) FOC_Stop();
            else FOC_Run();
        }
    }
}

/* TIM6 1 kHz ISR — encoder read + V/f control loop.
 * Priority 1: below ADC (0), above UART (2). */
static void TIM6_Init_1kHz(void) {
    RCC->APB1ENR1 |= RCC_APB1ENR1_TIM6EN;
    (void)RCC->APB1ENR1;  /* sync after clock enable */
    TIM6->PSC = 169;   /* 170 MHz / 170 = 1 MHz */
    TIM6->ARR = 999;   /* 1 MHz / 1000 = 1 kHz */
    TIM6->SR  = 0;     /* clear UIF before enable — prevent spurious IRQ */
    TIM6->CR1 |= TIM_CR1_ARPE;  /* preload ARR (RM0440 recommendation) */
    TIM6->DIER |= TIM_DIER_UIE;
    TIM6->CR1 |= TIM_CR1_CEN;
    NVIC_SetPriority(TIM6_DAC_IRQn, 2);  /* ниже TIM2 (encoder capture, ревью Bolt P2) */
    NVIC_EnableIRQ(TIM6_DAC_IRQn);
}

/* vflog: единый телеметрический пакет V/f-сессии (ТЗ TZ_VF_DATA_LOGGING.md).
 * Публикуется из TIM6_DAC_IRQHandler (приоритет 1) — ОБЯЗАТЕЛЬНО через
 * UART_TrySendTelemetry() (неблокирующий), а не UART_SendTelemetry(), иначе
 * при заполнении UART TX-буфера возможен priority-inversion deadlock
 * (TIM6_DAC_IRQn=1 не может быть вытеснен USART2_IRQn=2). */
static volatile uint32_t vflog_period_ms = 0;
static volatile uint32_t vflog_last_ms = 0;
#define VFLOG_DEFAULT_PERIOD_MS  20u  /* 50 Гц — запас от лимита UART 115200 бод */

void TIM6_DAC_IRQHandler(void) {
    if(TIM6->SR & TIM_SR_UIF) {
        TIM6->SR &= ~TIM_SR_UIF;  /* &= — не записывать 1 в прочие биты (ревью п.12) */
        ENC_Update();
        if(VFC_IsRunning()) {
            ADC_StartConversion();  /* regular group — refresh adc_data for PROTECT_Check */
            VFC_Update();
            PROTECT_Check();
            if(PROTECT_IsFault()) { VFC_Stop(); vflog_period_ms = 0; TRIG_Low(); }
            else if(vflog_period_ms > 0 && (sys_tick_ms - vflog_last_ms) >= vflog_period_ms) {
                vflog_last_ms = sys_tick_ms;
                UART_TrySendTelemetry(
                    "@VFLOG:t=%lu:target=%ld:meas=%ld:fe=%ld:fslip=%ld:vmag=%ld:theta=%lu:"
                    "du=%ld:dv=%ld:dw=%ld:i1=%u:i2=%u:ires=%u:vbus=%u:"
                    "eangle=%u:espeed=%ld:eerr=%u:fault=%d\r\n",
                    (unsigned long)sys_tick_ms,
                    (long)vfc.target_rpm, (long)vfc.measured_rpm, (long)vfc.f_e_hz,
                    (long)vfc.f_slip_hz, (long)vfc.voltage_mag, (unsigned long)vfc.theta_elec,
                    (long)vfc.duty_u, (long)vfc.duty_v, (long)vfc.duty_w,
                    (unsigned)ADC_GetRawI1(), (unsigned)ADC_GetRawI2(),
                    (unsigned)ADC_GetRawIres(), (unsigned)ADC_GetRawVbus(),
                    (unsigned)ENC_GetAngle14(), (long)ENC_GetSpeed_rpm(),
                    (unsigned)ENC_GetError(), (int)PROTECT_GetFaultReason());
            }
        }
    }
}

static void print_help(void) {
    DBG_STR("1=start 0=stop s=500=spd i=id,iq f=clear m=menu\r\n"
                 "idle  curve  irot  inertia  params\r\n"
                 "ch       - detect current channel\r\n"
                 "iv       - multi-point Rs (I-V)\r\n"
                 "pairs    - measure A/B/C\r\n"
                 "abort    - abort running autotune\r\n"
                 "stats    - print Rs/Ls/Isat statistics\r\n"
                 "oew      - Ls via both inverters (OEW)\r\n"
                 "rr       - Rr test (5 Hz, locked rotor)\r\n"
                 "noload   - Lm/Lr test (V/f, free rotor)\r\n"
                 "scope    - current oscilloscope (100 pts)\r\n"
                 "pi=N     - calc PI gains (N=bandwidth Hz) + apply\r\n"
                 "piapply  - apply last calculated Kp/Ki to FOC\r\n"
                 "mp=R,L,Rr,Lm,Tr,Ke,p,J - apply motor params to FOC\r\n"
                 "mpapply  - apply g_motor_params to FOC (no args)\r\n"
                 "lspos    - Ls vs rotor position (6 pts)\r\n"
                 "DBG: p=arr,duty,dt[,mask] a a=N c p? dump dump8 pdump\r\n");
}

int main(void) {
    SystemCoreClockUpdate();
    FLASH->ACR = (FLASH->ACR & ~FLASH_ACR_LATENCY) | FLASH_ACR_LATENCY_4WS;
    RCC->CR &= ~RCC_CR_PLLON;
    while(RCC->CR & RCC_CR_PLLRDY);
    RCC->PLLCFGR = (3U  << RCC_PLLCFGR_PLLM_Pos)
                 | (85U << RCC_PLLCFGR_PLLN_Pos)
                 | (0U  << RCC_PLLCFGR_PLLR_Pos)
                 | RCC_PLLCFGR_PLLREN
                 | (2U  << RCC_PLLCFGR_PLLSRC_Pos);
    RCC->CR |= RCC_CR_PLLON;
    while(!(RCC->CR & RCC_CR_PLLRDY));
    RCC->CFGR = (RCC->CFGR & ~RCC_CFGR_SW) | RCC_CFGR_SW_PLL;
    while((RCC->CFGR & RCC_CFGR_SWS) != RCC_CFGR_SWS_PLL);
    SystemCoreClockUpdate();

    UART_Init();
    UART_SendTelemetry("OEW FOC v0.2 @%luMHz\r\n> ", (unsigned long)(SystemCoreClock / 1000000));
    SWO_Init();
    /* НЕ выводим в SWO при инициализации: ITM FIFO забивается ДО подключения
     * отладчика → ITM_TCR_BUSY навсегда (OpenOCD не может прочитать TCR).
     * SWO-вывод — только по команде 's', когда TPI уже настроен отладчиком. */
    GPIO_Init();
    ADC_Init(); UART_SendStr("ADC OK\r\n");
    PWM_Init(); UART_SendStr("PWM OK\r\n");
    CORDIC_Init(); UART_SendStr("CORDIC OK\r\n");
    PROTECT_Init(); UART_SendStr("PROTECT OK\r\n");
    FOC_Init(); UART_SendStr("FOC init OK\r\n");
    ADC_InjectedInit(); UART_SendStr("ADC injected OK\r\n");
    Autotune_Init(); UART_SendStr("Autotune OK\r\n");
    ENC_Init();      UART_SendStr("Encoder OK\r\n");
    VFC_Init();      UART_SendStr("V/f Ctrl OK\r\n");
    /* SysTick ДО TIM6 (ревью main.c, п.4): TIM6 ISR использует sys_tick_ms —
     * иначе первые миллисекунды после старта TIM6 читают sys_tick_ms=0. */
    SysTick_Config(SystemCoreClock / 1000U);
    NVIC_SetPriority(SysTick_IRQn, 3);  /* ниже ADC(0) и TIM6(1) — п.17 */
    TIM6_Init_1kHz();
    NVIC_SetPriority(ADC1_2_IRQn, 0);
    NVIC_EnableIRQ(ADC1_2_IRQn);
    print_help();
    UART_SendStr("> ");
    uint32_t last_telem_ms = 0, last_adc_stream_ms = 0, adc_stream_period_ms = 0;
    while(1) {
        char linebuf[64];
        int rc = UART_ReadLine(linebuf, sizeof(linebuf));
        if(rc > 0) {
            unsigned int u1, u2, u3, u4;
            int a1=0, a2=0, a3=0, a4=0, a5=0, a6=0, a7=0, a8=0;
            if(strcmp(linebuf, "a") == 0) {
                ADC_StartConversion();
                UART_SendTelemetry("@ADC:I1=%u:I2=%u:Ires=%u:VBUS=%u\r\n> ", ADC_GetRawI1(), ADC_GetRawI2(), ADC_GetRawIres(), ADC_GetRawVbus());
            }
            else if(sscanf(linebuf, "a=%u", &u1) == 1) {
                if(u1 == 0) { adc_stream_period_ms = 0; DBG_STR("ADC stream stopped\r\n> "); }
                else if(u1 >= 50 && u1 <= 1000) { adc_stream_period_ms = u1; last_adc_stream_ms = sys_tick_ms; DBG_FMT("ADC stream started: %u ms\r\n> ", u1); }
                else { DBG_STR("err: N must be 0 or 50..1000\r\n> "); }
            }
            else if(strcmp(linebuf, "a?") == 0) { UART_SendTelemetry("@ADC:STATUS:offset_i1=%u:stream=%lu\r\n> ", ADC_GetOffsetI1(), (unsigned long)adc_stream_period_ms); }
            else if(strcmp(linebuf, "c") == 0) {
                /* Калибровка single-shot'ами НЕСОВМЕСТИМА с вооружённой
                 * injected-группой (JADSTART ждёт TIM1_TRGO) — жёсткий запрет
                 * при работающем PWM (ревью pwm.c+adc.c, P1). */
                if(PWM_IsEnabled())
                    UART_SendStr("err: PWM running — stop FOC/Vf first\r\n> ");
                else {
                    /* Ревью п.8: JEOSIE остаётся включённым, а калибровка сама
                     * гоняет injected программно → ISR мог бы читать JDR
                     * параллельно. Отключаем ADC IRQ на время калибровки
                     * (как в autotune-командах). */
                    NVIC_DisableIRQ(ADC1_2_IRQn);
                    ADC_CalibrateOffsets_256();
                    NVIC_EnableIRQ(ADC1_2_IRQn);
                    UART_SendTelemetry("@ADC:CAL:offset_i1=%u:offset_i2=%u:offset_ires=%u\r\n> ", ADC_GetOffsetI1(), ADC_GetOffsetI2(), ADC_GetOffsetIres());
                }
            }
            else if(strcmp(linebuf, "p?") == 0) {
                uint32_t cr1,ccer,bdtr,cnt; PWM_GetStatus(&cr1,&ccer,&bdtr,&cnt);
                UART_SendTelemetry("@PWM:CR1=%lu:CCER=%lu:BDTR=%lu:CNT=%lu\r\n> ", (unsigned long)cr1,(unsigned long)ccer,(unsigned long)bdtr,(unsigned long)cnt);
            }
            else if(sscanf(linebuf, "p=%u,%u,%u,%u", &u1, &u2, &u3, &u4) >= 3) {
                /* P0 (ревью pwm.c, п.8/10): debug-модуляция переконфигурирует
                 * TIM1/8 и делает UG — при вооружённой injected-группе
                 * (JADSTART=1) UG дал бы ложный TRGO и битое состояние ADC.
                 * Запрет при работающем PWM (как в команде 'c'). */
                if(PWM_IsEnabled())
                    UART_SendStr("err: PWM running — stop FOC/Vf first\r\n> ");
                else {
                    if(u4 == 0) { u4 = 0x3F; }
                    PWM_DebugSetModulation((uint16_t)u1, (uint16_t)u2, u3, (uint8_t)u4);
                    UART_SendTelemetry("@PWM:OK:arr=%u:duty=%u:dt=%u\r\n> ", u1, u2, u3);
                }
            }
            else if(linebuf[0] == '1' && linebuf[1] == '\0') {
                if(PROTECT_IsFault()) DBG_STR("FAULT! send 'f' to clear\r\n> ");
                else { VFC_Stop(); FOC_Start(); DBG_STR("FOC started\r\n> "); }
            }
            else if(linebuf[0] == '0' && linebuf[1] == '\0') { FOC_Stop(); DBG_STR("FOC stopped\r\n> "); }
            else if(linebuf[0] == 'm' && linebuf[1] == '\0') { print_help(); }
            else if(linebuf[0] == 's' && linebuf[1] == '\0') {
                SWO_Printf("@SWO:test:tick=%lu\r\n", (unsigned long)sys_tick_ms);
                UART_SendStr("SWO test sent\r\n> ");
            }
            else if(linebuf[0] == 'f' && linebuf[1] == '\0') {
                PROTECT_Clear();
                /* Ревью п.9: !FOC_IsRunning() недостаточно — при работающем
                 * V/f (FOC=false, VFC=true) калибровка на живом инверторе
                 * дала бы ложные offsets. Калибруем только при полном стопе. */
                if (!FOC_IsRunning() && !VFC_IsRunning()) { ADC_CalibrateOffsets(); }
                DBG_STR("fault cleared\r\n> ");
            }
            else if(linebuf[0] == 's' && linebuf[1] == '=') {
                long rpm_tmp = 0; char trail = '\0';  /* п.19 ревью: %ld → long, без (long*)&int32_t */
                int f = sscanf(linebuf + 2, "%ld%c", &rpm_tmp, &trail);
                int32_t rpm = (int32_t)rpm_tmp;
                if(f < 1) UART_SendStr("err: no digits\r\n> ");
                else if(f > 1 && trail != '\0') UART_SendStr("err: trailing chars\r\n> ");
                else if(rpm > 50000 || rpm < -50000) UART_SendStr("err: out of range\r\n> ");
                else { FOC_SetSpeed(rpm); DBG_FMT("speed=%ld rpm\r\n> ", (long)FOC_GetSpeed()); }
            } else if(strcmp(linebuf, "dump") == 0) {
                uint32_t psc, arr, bdtr, cr1, cr2, ccer;
                PWM_DumpRegs(&psc, &arr, &bdtr, &cr1, &cr2, &ccer);
                UART_SendTelemetry("@PWM:DUMP:PSC=%lu:ARR=%lu:BDTR=0x%08lX:CR1=0x%08lX:CR2=0x%08lX:CCER=0x%08lX\r\n> ",
                    (unsigned long)psc, (unsigned long)arr, (unsigned long)bdtr,
                    (unsigned long)cr1, (unsigned long)cr2, (unsigned long)ccer);
            } else if(strcmp(linebuf, "dumpa") == 0) {
                /* Diagnostic: all ADC2 config registers */
                UART_SendTelemetry("@ADUMP:SQR1=0x%08lX:CFGR=0x%08lX:SMPR1=0x%08lX:JSQR=0x%08lX:DIFSEL=0x%08lX:CR=0x%08lX:ISR=0x%08lX:DR=0x%04lX:JDR1=0x%04lX:JDR2=0x%04lX:JDR3=0x%04lX:JDR4=0x%04lX\r\n> ",
                    (unsigned long)ADC2->SQR1, (unsigned long)ADC2->CFGR,
                    (unsigned long)ADC2->SMPR1, (unsigned long)ADC2->JSQR,
                    (unsigned long)ADC2->DIFSEL, (unsigned long)ADC2->CR,
                    (unsigned long)ADC2->ISR, (unsigned long)ADC2->DR,
                    (unsigned long)ADC2->JDR1, (unsigned long)ADC2->JDR2,
                    (unsigned long)ADC2->JDR3, (unsigned long)ADC2->JDR4);
            } else if(strcmp(linebuf, "dump8") == 0) {
                uint32_t psc, arr, bdtr, cr1, cr2, ccer;
                PWM_DumpRegs8(&psc, &arr, &bdtr, &cr1, &cr2, &ccer);
                UART_SendTelemetry("@PWM8:DUMP:PSC=%lu:ARR=%lu:BDTR=0x%08lX:CR1=0x%08lX:CR2=0x%08lX:CCER=0x%08lX\r\n> ",
                    (unsigned long)psc, (unsigned long)arr, (unsigned long)bdtr,
                    (unsigned long)cr1, (unsigned long)cr2, (unsigned long)ccer);
            } else if(strcmp(linebuf, "pdump") == 0) {
                UART_SendTelemetry("@PWM:FULL:SYS=%lu:CFGR=0x%08lX:T1:PSC=%u:ARR=%u:CCR=%u,%u,%u:BDTR=0x%08lX:CCER=0x%08lX:CR1=0x%08lX:CNT=%lu:T8:PSC=%u:ARR=%u:CCR=%u,%u,%u:BDTR=0x%08lX:CCER=0x%08lX:CR1=0x%08lX:CNT=%lu\r\n> ",
                    (unsigned long)SystemCoreClock, (unsigned long)RCC->CFGR,
                    (unsigned)TIM1->PSC, (unsigned)TIM1->ARR,
                    (unsigned)TIM1->CCR1, (unsigned)TIM1->CCR2, (unsigned)TIM1->CCR3,
                    (unsigned long)TIM1->BDTR, (unsigned long)TIM1->CCER, (unsigned long)TIM1->CR1,
                    (unsigned long)TIM1->CNT,
                    (unsigned)TIM8->PSC, (unsigned)TIM8->ARR,
                    (unsigned)TIM8->CCR1, (unsigned)TIM8->CCR2, (unsigned)TIM8->CCR3,
                    (unsigned long)TIM8->BDTR, (unsigned long)TIM8->CCER, (unsigned long)TIM8->CR1,
                    (unsigned long)TIM8->CNT);
            } else if(strcmp(linebuf, "sysinfo") == 0) {
                uint32_t psc, tclk;
                PWM_GetSysInfo(&psc, &tclk);
                UART_SendTelemetry("@SYS:CLK=%lu:PSC=%lu:TCLK=%lu:PLLCFGR=0x%08lx:OVR=%lu:JEOS=%lu:TO=%lu\r\n> ",
                    (unsigned long)SystemCoreClock, (unsigned long)psc, (unsigned long)tclk,
                    (unsigned long)RCC->PLLCFGR, (unsigned long)ADC_GetOvrCount(),
                    (unsigned long)ADC_GetJeosCount(), (unsigned long)ADC_GetTimeoutCount());
            } else if(sscanf(linebuf, "pp=%u", &u1) == 1) {
                if(u1 < 1 || u1 > 24) UART_SendStr("err: pole pairs must be 1..24\r\n> ");
                else { FOC_SetPolePairs((uint8_t)u1); g_motor_params.pole_pairs = (uint8_t)u1; UART_SendTelemetry("pole_pairs=%u\r\n> ", u1); }
            } else if(sscanf(linebuf, "fwbase=%u", &u1) == 1) {
                /* FW-01: базовая скорость ослабления поля (speed gate). */
                if(u1 < 100 || u1 > 5000) UART_SendStr("err: FW base speed must be 100..5000 rpm\r\n> ");
                else if(FOC_SetBaseSpeed((int32_t)u1) == 0)
                    UART_SendTelemetry("fw_base_speed=%u rpm\r\n> ", u1);
                else UART_SendStr("err: can't set base speed\r\n> ");
            } else if(sscanf(linebuf, "dt=%u", &u1) == 1) {
                if(u1 > 12700) UART_SendStr("err: max 12700 ns\r\n> ");
                else if(PWM_SetDeadTime_ns(u1) != 0)
                    UART_SendStr("err: PWM running — stop FOC/Vf first\r\n> ");
                else
                    UART_SendTelemetry("@PWM:DT=%u ns (DTG=%lu)\r\n> ", u1, (unsigned long)(TIM1->BDTR & 0xFF));
            } else if(strcmp(linebuf, "curve") == 0) {
                Autotune_PrintCurve();
                UART_SendStr("\r\n> ");
            } else if(strcmp(linebuf, "params") == 0) {
                Autotune_PrintParams();
                UART_SendTelemetry("@AP:%d\r\n> ", FOC_IsParamsApplied());
            } else if(strcmp(linebuf, "irot") == 0) {
                int8_t r = Autotune_Irot();
                if(r == 0) UART_SendStr("@IROT:OK\r\n> ");
                else       UART_SendStr("@IROT:FAIL\r\n> ");
            } else if(strcmp(linebuf, "inertia") == 0) {
                int8_t r = Autotune_Inertia();
                if(r == 0) UART_SendStr("@INERTIA:OK\r\n> ");
                else       UART_SendStr("@INERTIA:FAIL\r\n> ");
            } else if(strcmp(linebuf, "ch") == 0) {
                NVIC_DisableIRQ(ADC1_2_IRQn);
                int8_t _r = Autotune_DetectChannel();
                NVIC_EnableIRQ(ADC1_2_IRQn);
                if(_r == 0) UART_SendStr("@AT:CH:OK\r\n> ");
                else       UART_SendStr("@AT:CH:FAIL\r\n> ");
            } else if(strcmp(linebuf, "iv") == 0) {
                NVIC_DisableIRQ(ADC1_2_IRQn);
                int8_t _r = Autotune_MeasureRs_IV();
                NVIC_EnableIRQ(ADC1_2_IRQn);
                if(_r == 0) UART_SendStr("@AT:IV:OK\r\n> ");
                else       UART_SendStr("@AT:IV:FAIL\r\n> ");
            } else if(strcmp(linebuf, "pairs") == 0) {
                NVIC_DisableIRQ(ADC1_2_IRQn);
                int8_t _r = Autotune_MeasureAllPairs();
                NVIC_EnableIRQ(ADC1_2_IRQn);
                if(_r == 0) UART_SendStr("@AT:PAIRS:RESULT_OK\r\n> ");
                else       UART_SendStr("@AT:PAIRS:RESULT_FAIL\r\n> ");
            } else if(strcmp(linebuf, "abort") == 0) {
                g_autotune_abort = 1;
                UART_SendStr("abort requested\r\n> ");
            } else if(strcmp(linebuf, "oew") == 0) {
                g_autotune_abort = 0;
                NVIC_DisableIRQ(ADC1_2_IRQn); int8_t _ro = Autotune_MeasureLs_OEW(); NVIC_EnableIRQ(ADC1_2_IRQn);
                if(_ro == 0) UART_SendStr("@AT:OEW:RESULT_OK\r\n> "); else if(_ro == -5) UART_SendStr("@AT:OEW:ABORTED\r\n> "); else UART_SendStr("@AT:OEW:RESULT_FAIL\r\n> ");
            } else if(strcmp(linebuf, "rr") == 0) {
                g_autotune_abort = 0;
                NVIC_DisableIRQ(ADC1_2_IRQn); int8_t _rr = Autotune_MeasureRr(); NVIC_EnableIRQ(ADC1_2_IRQn);
                if(_rr == 0) UART_SendStr("@AT:RR:RESULT_OK\r\n> "); else if(_rr == -6) UART_SendStr("@AT:RR:ABORTED\r\n> "); else UART_SendStr("@AT:RR:RESULT_FAIL\r\n> ");
            } else if(strcmp(linebuf, "noload") == 0) {
                g_autotune_abort = 0;
                NVIC_DisableIRQ(ADC1_2_IRQn); int8_t _rn = Autotune_MeasureNoLoad(); NVIC_EnableIRQ(ADC1_2_IRQn);
                if(_rn == 0) UART_SendStr("@AT:NOLOAD:RESULT_OK\r\n> "); else if(_rn == -6) UART_SendStr("@AT:NOLOAD:ABORTED\r\n> "); else UART_SendStr("@AT:NOLOAD:RESULT_FAIL\r\n> ");
            } else if(strcmp(linebuf, "scope") == 0) {
                g_autotune_abort = 0;
                NVIC_DisableIRQ(ADC1_2_IRQn); int8_t _rs = Autotune_Scope(); NVIC_EnableIRQ(ADC1_2_IRQn);
                if(_rs == 0) UART_SendStr("@SCOPE:RESULT_OK\r\n> "); else UART_SendStr("@SCOPE:RESULT_FAIL\r\n> ");
            } else if(sscanf(linebuf, "pi=%u", &u1) == 1) {
                Autotune_CalcPI((int32_t)u1);
                UART_SendStr("> ");
            } else if(strcmp(linebuf, "lspos") == 0) {
                g_autotune_abort = 0;
                NVIC_DisableIRQ(ADC1_2_IRQn); int8_t _rl = Autotune_MeasureLs_Position(); NVIC_EnableIRQ(ADC1_2_IRQn);
                if(_rl == 0) UART_SendStr("@AT:LSPOS:RESULT_OK\r\n> "); else if(_rl == -5) UART_SendStr("@AT:LSPOS:ABORTED\r\n> "); else UART_SendStr("@AT:LSPOS:RESULT_FAIL\r\n> ");
            } else if(sscanf(linebuf, "mp=%d,%d,%d,%d,%d,%d,%d,%d", &a1,&a2,&a3,&a4,&a5,&a6,&a7,&a8) >= 2) {
                int _rc = FOC_SetMotorParams(a1, a2, (int32_t)ADC_GetVbus_mV());
                if(_rc == 0) {
                    if(a7 >= 1 && a7 <= 24) FOC_SetPolePairs(a7);
                    g_motor_params.Rs_mOhm = a1;
                    g_motor_params.Ls_uH   = a2;
                    if(a3 > 0) g_motor_params.Rr_mOhm = a3;
                    if(a4 > 0) g_motor_params.Lm_uH   = a4;
                    if(a5 > 0) g_motor_params.Tr_rotor_us   = a5;
                    if(a6 > 0) g_motor_params.Ke_mV_per_rpm = a6;
                    if(a7 > 0) g_motor_params.pole_pairs = (uint8_t)a7;
                    if(a8 > 0) g_motor_params.J_kg_m2_x1e6 = a8;
                    g_motor_params.measured_mask |= AT_VALID_RS | AT_VALID_LS;
                    if(a3 > 0) g_motor_params.measured_mask |= AT_VALID_RR;
                    if(a4 > 0) g_motor_params.measured_mask |= AT_VALID_LM;
                    if(a5 > 0) g_motor_params.measured_mask |= AT_VALID_TR;
                    if(a6 > 0) g_motor_params.measured_mask |= AT_VALID_KE;
                    if(a7 > 0) g_motor_params.measured_mask |= AT_VALID_PAIRS;
                    if(a8 > 0) g_motor_params.measured_mask |= AT_VALID_J;
                    int32_t _kp, _ki, _lsig;
                    FOC_GetMotorParams(NULL, NULL, &_kp, &_ki);
                    _lsig = FOC_GetSigmaL_uH();
                    UART_SendTelemetry("@MP:OK:Rs=%d:Ls=%d:Rr=%d:Lm=%d:Tr=%d:Ke=%d:p=%d:J=%d:Kp=%d:Ki=%d:Lsig=%d:AP=1\r\n> ", a1,a2,a3,a4,a5,a6,a7,a8,_kp,_ki,_lsig);
                } else {
                    UART_SendTelemetry("@MP:ERROR:%d\r\n> ", _rc);
                }
            } else if(strcmp(linebuf, "mpapply") == 0) {
                int _rc = FOC_SetMotorParams(g_motor_params.Rs_mOhm,
                                             g_motor_params.Ls_uH,
                                             ADC_GetVbus_mV());
                if(_rc == 0) {
                    if(g_motor_params.pole_pairs >= 1 && g_motor_params.pole_pairs <= 24)
                        FOC_SetPolePairs(g_motor_params.pole_pairs);
                    int32_t _kp, _ki;
                    FOC_GetMotorParams(NULL, NULL, &_kp, &_ki);
                    int32_t _lsig = FOC_GetSigmaL_uH();
                    UART_SendTelemetry("@MPAPPLY:OK:Rs=%ld:Ls=%ld:Rr=%ld:Lm=%ld:Tr=%ld:p=%d:Kp=%ld:Ki=%ld:Lsig=%ld:AP=1\r\n> ",
                        (long)g_motor_params.Rs_mOhm, (long)g_motor_params.Ls_uH,
                        (long)g_motor_params.Rr_mOhm, (long)g_motor_params.Lm_uH,
                        (long)g_motor_params.Tr_rotor_us, (int)g_motor_params.pole_pairs,
                        (long)_kp, (long)_ki, (long)_lsig);
                } else {
                    UART_SendTelemetry("@MPAPPLY:ERROR:%d\r\n> ", _rc);
                }
            } else if(strcmp(linebuf, "piapply") == 0) {
                int32_t _kp, _ki, _bw;
                if(Autotune_GetLastPI(&_kp, &_ki, &_bw) == 0) {
                    int _rc = FOC_SetPIGains(_kp, _ki);
                    if(_rc == 0) UART_SendTelemetry("@PI:APPLIED:Kp=%ld:Ki=%ld:AP=1\r\n> ", (long)_kp, (long)_ki);
                    else UART_SendTelemetry("@PI:ERROR:%d\r\n> ", _rc);
                } else {
                    UART_SendStr("@PI:ERROR:NOT_CALCULATED\r\n> ");
                }
            } else if(strcmp(linebuf, "stats") == 0) {
                Autotune_PrintStats();
                UART_SendStr("> ");
            } else if(strcmp(linebuf, "idle") == 0) {
                g_autotune_abort = 0;
                NVIC_DisableIRQ(ADC1_2_IRQn);
                int8_t _r = Autotune_Idle();
                NVIC_EnableIRQ(ADC1_2_IRQn);
                if(_r == 0)      UART_SendStr("@IDLE:OK\r\n> ");
                else if(_r == -5) UART_SendStr("@IDLE:ABORTED\r\n> ");
                else             UART_SendStr("@IDLE:FAIL\r\n> ");
            }
            /* ── V/f control + encoder commands ── */
            else if(sscanf(linebuf, "vf=%d", &a1) == 1) {
                if(a1 == 0) {
                    VFC_Stop();
                    vflog_period_ms = 0;   /* авто-стоп лога вместе с V/f */
                    TRIG_Low();
                    UART_SendStr("V/f stopped\r\n> ");
                } else if(a1 >= -5000 && a1 <= 5000) {
                    if(PROTECT_IsFault()) {
                        UART_SendStr("FAULT! send 'f' to clear\r\n> ");
                    } else {
                        FOC_Stop();
                        /* Аппаратный триггер СРАЗУ перед VFC_Start(): фронт PB6 виден
                         * на sigrok, а trig_tick — тот же sys_tick_ms, что публикуется
                         * в @VFLOG:t=... — точная привязка UART-лога к захвату лог.
                         * анализатора без программной оценки задержки USB/UART. */
                        TRIG_High();
                        uint32_t trig_tick = sys_tick_ms;
                        VFC_Start(a1);
                        /* авто-старт лога вместе с V/f, если не включен вручную заранее */
                        if(vflog_period_ms == 0) vflog_period_ms = VFLOG_DEFAULT_PERIOD_MS;
                        vflog_last_ms = sys_tick_ms;
                        UART_SendTelemetry("V/f started: %d rpm\r\n@TRIG:tick=%lu\r\n> ",
                            a1, (unsigned long)trig_tick);
                    }
                } else {
                    UART_SendStr("err: rpm range -5000..+5000\r\n> ");
                }
            } else if(sscanf(linebuf, "vflog=%u", &u1) == 1) {
                if(u1 == 0) { vflog_period_ms = 0; UART_SendStr("vflog stopped\r\n> "); }
                else if(u1 >= 10 && u1 <= 1000) {
                    vflog_period_ms = u1; vflog_last_ms = sys_tick_ms;
                    UART_SendTelemetry("vflog started: %u ms\r\n> ", u1);
                } else { UART_SendStr("err: N must be 0 or 10..1000\r\n> "); }
            } else if(strcmp(linebuf, "vf?") == 0) {
                UART_SendTelemetry("@VF:target=%ld:meas=%ld:fe=%ld:fslip=%ld:vmag=%ld\r\n> ",
                    (long)VFC_GetTarget(), (long)VFC_GetSpeed(),
                    (long)vfc.f_e_hz, (long)vfc.f_slip_hz, (long)vfc.voltage_mag);
            } else if(strcmp(linebuf, "enc") == 0) {
                UART_SendTelemetry("@ENC:angle=%u:speed=%ld:period_us=%lu:pulse_us=%lu:err=%u\r\n> ",
                    (unsigned)ENC_GetAngle14(), (long)ENC_GetSpeed_rpm(),
                    (unsigned long)ENC_GetPeriod_us(), (unsigned long)ENC_GetPulseWidth_us(),
                    (unsigned)ENC_GetError());
            } else if(sscanf(linebuf, "vfk=%d,%d", &a1, &a2) == 2) {
                VFC_SetVfParams(a1, a2);
                UART_SendTelemetry("V/f params: boost=%d%% rated=%dHz\r\n> ", a1, a2);
            } else {
                UART_SendStr("unknown\r\n> ");
            }
        } else if(rc < 0) UART_SendStr("line overflow\r\n> ");

        if(adc_stream_period_ms > 0 && (sys_tick_ms - last_adc_stream_ms) >= adc_stream_period_ms) {
            last_adc_stream_ms = sys_tick_ms; ADC_StartConversion();
            UART_SendTelemetry("@ADC:I1=%u:I2=%u:Ires=%u:VBUS=%u\r\n", ADC_GetRawI1(), ADC_GetRawI2(), ADC_GetRawIres(), ADC_GetRawVbus());
        }
        if(adc_stream_period_ms == 0 && (sys_tick_ms - last_telem_ms) >= 100) {
            last_telem_ms = sys_tick_ms;
            if(VFC_IsRunning()) {
                UART_SendTelemetry("@VF:target=%ld:meas=%ld:fe=%ld:fslip=%ld:vmag=%ld\r\n",
                    (long)VFC_GetTarget(), (long)VFC_GetSpeed(),
                    (long)vfc.f_e_hz, (long)vfc.f_slip_hz, (long)vfc.voltage_mag);
            } else {
                UART_SendTelemetry("@FOC:I1=%ld:I2=%ld:Ires=%ld:VBUS=%ld:STATE=%u:SPD=%ld:TH=%ld:FAULT=%d:FAULT_R=%d\r\n",
                    ADC_GetI1_mA(), ADC_GetI2_mA(), ADC_GetIres_mA(), ADC_GetVbus_mV(),
                    (unsigned)FOC_GetState(), (long)FOC_GetMeasSpeedRPM(),
                    (long)FOC_GetThetaMilliRad(), PROTECT_IsFault(), PROTECT_GetFaultReason());
            }
        }
    }
}
