#include "stm32g474xx.h"
#include "pwm.h"
#include "adc.h"
#include "uart.h"
#include "cordic_math.h"
#include "observer.h"
#include "pll.h"
#include "foc.h"
#include "protect.h"
#include <string.h>
#include <stdio.h>

static volatile uint32_t sys_tick_ms = 0;
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
    GPIOB->BSRR = (1U<<20)|(1U<<21);
    GPIOA->MODER |= (3U<<0)|(3U<<2)|(3U<<12);
    GPIOC->MODER |= (3U<<8);
}

void ADC1_2_IRQHandler(void) {
    if(ADC2->ISR & ADC_ISR_JEOS) {
        ADC2->ISR = ADC_ISR_JEOS;
        ADC_ReadInjected();
        if(FOC_IsRunning()) {
            PROTECT_Check();
            if(PROTECT_IsFault()) FOC_Stop();
            else FOC_Run();
        }
    }
}

int main(void) {
    SystemCoreClockUpdate();

    /* Flash: 4 wait states for 170 MHz (ДО переключения на PLL) */
    FLASH->ACR = (FLASH->ACR & ~FLASH_ACR_LATENCY) | FLASH_ACR_LATENCY_4WS;

    /* Выключить PLL перед переконфигурацией */
    RCC->CR &= ~RCC_CR_PLLON;
    while(RCC->CR & RCC_CR_PLLRDY);

    /* PLL: HSI 16 MHz -> PLL -> 170 MHz
     * VCO = 16 / PLLM x PLLN = 16 / 2 x 85 = 680 MHz
     * CLK = VCO / PLLR = 680 / 4 = 170 MHz */
    RCC->PLLCFGR = (2U << RCC_PLLCFGR_PLLM_Pos)       /* PLLM = 2 */
                 | (85U << RCC_PLLCFGR_PLLN_Pos)       /* PLLN = 85 */
                 | (1U << RCC_PLLCFGR_PLLR_Pos)        /* PLLR = 001 -> /4 */
                 | RCC_PLLCFGR_PLLREN                  /* PLLR output (sysclk) */
                 | RCC_PLLCFGR_PLLQEN                  /* PLLQ output */
                 | (2U << RCC_PLLCFGR_PLLSRC_Pos);     /* PLLSRC = 10 -> HSI16 */

    RCC->CR |= RCC_CR_PLLON;
    while(!(RCC->CR & RCC_CR_PLLRDY));
    RCC->CFGR = (RCC->CFGR & ~RCC_CFGR_SW) | RCC_CFGR_SW_PLL;
    while((RCC->CFGR & RCC_CFGR_SWS) != RCC_CFGR_SWS_PLL);
    SystemCoreClockUpdate();

    UART_Init();
    UART_SendTelemetry("OEW FOC v0.2 @%luMHz\r\n> ", (unsigned long)(SystemCoreClock / 1000000));
    GPIO_Init();
    ADC_Init(); UART_SendStr("ADC OK\r\n");
    PWM_Init(); UART_SendStr("PWM OK\r\n");
    CORDIC_Init(); UART_SendStr("CORDIC OK\r\n");
    PROTECT_Init(); UART_SendStr("PROTECT OK\r\n");
    FOC_Init(); UART_SendStr("FOC init OK\r\n");
    ADC_InjectedInit(); UART_SendStr("ADC injected OK\r\n");
    SysTick_Config(SystemCoreClock / 1000U);
    NVIC_SetPriority(ADC1_2_IRQn, 0);
    NVIC_EnableIRQ(ADC1_2_IRQn);
    UART_SendStr("Ready.\r\nCommands: 1=start 0=stop s=500=speed i=id,iq f=clear m=menu\r\nDBG: p=arr,duty,dt[,mask] a a=N c p? a?\r\n");
    UART_SendStr("> ");
    uint32_t last_telem_ms = 0, last_adc_stream_ms = 0, adc_stream_period_ms = 0;
    while(1) {
        char linebuf[32];
        int rc = UART_ReadLine(linebuf, sizeof(linebuf));
        if(rc > 0) {
            unsigned int u1, u2, u3, u4;
            if(strcmp(linebuf, "a") == 0) {
                ADC_StartConversion();
                UART_SendTelemetry("@ADC:I1=%u:I2=%u:IN=%u:VBUS=%u\r\n> ", ADC_GetRawI1(), ADC_GetRawI2(), ADC_GetRawIN(), ADC_GetRawVbus());
            }
            else if(sscanf(linebuf, "a=%u", &u1) == 1) {
                if(u1 == 0) { adc_stream_period_ms = 0; UART_SendStr("ADC stream stopped\r\n> "); }
                else if(u1 >= 50 && u1 <= 1000) { adc_stream_period_ms = u1; last_adc_stream_ms = sys_tick_ms; UART_SendTelemetry("ADC stream started: %u ms\r\n> ", u1); }
                else { UART_SendStr("err: N must be 0 or 50..1000\r\n> "); }
            }
            else if(strcmp(linebuf, "a?") == 0) { UART_SendTelemetry("@ADC:STATUS:offset=%u:stream=%lu\r\n> ", ADC_GetOffset(), (unsigned long)adc_stream_period_ms); }
            else if(strcmp(linebuf, "c") == 0) { ADC_CalibrateI1_256(); UART_SendTelemetry("@ADC:CAL:offset_i1=%u:offset_i2=%u:offset_in=%u\r\n> ", ADC_GetOffsetI1(), ADC_GetOffsetI2(), ADC_GetOffsetIN()); }
            else if(strcmp(linebuf, "p?") == 0) {
                uint32_t cr1,ccer,bdtr,cnt; PWM_GetStatus(&cr1,&ccer,&bdtr,&cnt);
                UART_SendTelemetry("@PWM:CR1=%lu:CCER=%lu:BDTR=%lu:CNT=%lu\r\n> ", (unsigned long)cr1,(unsigned long)ccer,(unsigned long)bdtr,(unsigned long)cnt);
            }
            else if(sscanf(linebuf, "p=%u,%u,%u,%u", &u1, &u2, &u3, &u4) >= 3) {
                if(u4 == 0) { u4 = 0x3F; }
                PWM_DebugConfig((uint16_t)u1, (uint16_t)u2, (uint8_t)u3, (uint8_t)u4);
                UART_SendTelemetry("@PWM:OK:arr=%u:duty=%u:dt=%u\r\n> ", u1, u2, u3);
            }
            else if(linebuf[0] == '1' && linebuf[1] == '\0') {
                if(PROTECT_IsFault()) UART_SendStr("FAULT! send 'f' to clear\r\n> ");
                else { FOC_Start(); UART_SendStr("FOC started\r\n> "); }
            }
            else if(linebuf[0] == '0' && linebuf[1] == '\0') { FOC_Stop(); UART_SendStr("FOC stopped\r\n> "); }
            else if(linebuf[0] == 'm' && linebuf[1] == '\0') { UART_SendStr("1=start 0=stop s=500=spd i=id,iq f=clear m=menu\r\n\nDBG: p=arr,duty,dt[,mask] a a=N c p? a?\r\n> "); }
            else if(linebuf[0] == 'f' && linebuf[1] == '\0') { PROTECT_Clear(); UART_SendStr("fault cleared\r\n> "); }
            else if(linebuf[0] == 's' && linebuf[1] == '=') {
                int32_t rpm = 0; char trail = '\0';
                int f = sscanf(linebuf + 2, "%ld%c", (long*)&rpm, &trail);
                if(f < 1) UART_SendStr("err: no digits\r\n> ");
                else if(f > 1 && trail != '\0') UART_SendStr("err: trailing chars\r\n> ");
                else if(rpm > 50000 || rpm < -50000) UART_SendStr("err: out of range\r\n> ");
                else { FOC_SetSpeed(rpm); UART_SendTelemetry("speed=%ld rpm\r\n> ", (long)FOC_GetSpeed()); }
            } else if(strcmp(linebuf, "dump") == 0) {
                uint32_t psc, arr, bdtr, cr1, cr2;
                PWM_DumpRegs(&psc, &arr, &bdtr, &cr1, &cr2);
                UART_SendTelemetry("@PWM:DUMP:PSC=%lu:ARR=%lu:BDTR=0x%08lX:CR1=0x%08lX:CR2=0x%08lX\r\n> ",
                    (unsigned long)psc, (unsigned long)arr, (unsigned long)bdtr,
                    (unsigned long)cr1, (unsigned long)cr2);
            } else if(strcmp(linebuf, "sysinfo") == 0) {
                uint32_t psc, tclk;
                PWM_GetSysInfo(&psc, &tclk);
                UART_SendTelemetry("@SYS:CLK=%lu:PSC=%lu:TCLK=%lu:PLLCFGR=0x%08lx\r\n> ",
                    (unsigned long)SystemCoreClock, (unsigned long)psc, (unsigned long)tclk, (unsigned long)RCC->PLLCFGR);
            } else UART_SendStr("unknown\r\n> ");
        } else if(rc < 0) UART_SendStr("line overflow\r\n> ");

        if(adc_stream_period_ms > 0 && (sys_tick_ms - last_adc_stream_ms) >= adc_stream_period_ms) {
            last_adc_stream_ms = sys_tick_ms; ADC_StartConversion();
            UART_SendTelemetry("@ADC:I1=%u:I2=%u:IN=%u:VBUS=%u\r\n", ADC_GetRawI1(), ADC_GetRawI2(), ADC_GetRawIN(), ADC_GetRawVbus());
        }
        if(adc_stream_period_ms == 0 && (sys_tick_ms - last_telem_ms) >= 100) {
            last_telem_ms = sys_tick_ms;
            UART_SendTelemetry("@FOC:I1=%ld:I2=%ld:IN=%ld:VBUS=%ld\r\n", ADC_GetI1_mA(), ADC_GetI2_mA(), ADC_GetIN_mA(), ADC_GetVbus_mV());
        }
    }
}
