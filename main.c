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
    GPIOB->BSRR = (1U<<4)|(1U<<5);   /* EN1, EN2 = HIGH */
    GPIOA->MODER |= (3U<<0)|(3U<<2)|(3U<<12);
    GPIOC->MODER |= (3U<<8);
}

void ADC1_2_IRQHandler(void) {
    if(ADC2->ISR & ADC_ISR_OVR) {
        ADC2->ISR = ADC_ISR_OVR;
        extern volatile uint32_t adc_ovr_count;
        adc_ovr_count++;
    }
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

static void print_help(void) {
    UART_SendStr("1=start 0=stop s=500=spd i=id,iq f=clear m=menu\r\n"
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
                 "lspos    - Ls vs rotor position (6 pts)\r\n"
                 "DBG: p=arr,duty,dt[,mask] a a=N c p? dump dump8\r\n");
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
    GPIO_Init();
    ADC_Init(); UART_SendStr("ADC OK\r\n");
    PWM_Init(); UART_SendStr("PWM OK\r\n");
    CORDIC_Init(); UART_SendStr("CORDIC OK\r\n");
    PROTECT_Init(); UART_SendStr("PROTECT OK\r\n");
    FOC_Init(); UART_SendStr("FOC init OK\r\n");
    ADC_InjectedInit(); UART_SendStr("ADC injected OK\r\n");
    Autotune_Init(); UART_SendStr("Autotune OK\r\n");
    SysTick_Config(SystemCoreClock / 1000U);
    NVIC_SetPriority(ADC1_2_IRQn, 0);
    NVIC_EnableIRQ(ADC1_2_IRQn);
    print_help();
    UART_SendStr("> ");
    uint32_t last_telem_ms = 0, last_adc_stream_ms = 0, adc_stream_period_ms = 0;
    while(1) {
        char linebuf[32];
        int rc = UART_ReadLine(linebuf, sizeof(linebuf));
        if(rc > 0) {
            unsigned int u1, u2, u3, u4;
            int a1=0, a2=0, a3=0, a4=0, a5=0, a6=0, a7=0, a8=0;
            if(strcmp(linebuf, "a") == 0) {
                ADC_StartConversion();
                UART_SendTelemetry("@ADC:I1=%u:I2=%u:Ires=%u:VBUS=%u\r\n> ", ADC_GetRawI1(), ADC_GetRawI2(), ADC_GetRawIres(), ADC_GetRawVbus());
            }
            else if(sscanf(linebuf, "a=%u", &u1) == 1) {
                if(u1 == 0) { adc_stream_period_ms = 0; UART_SendStr("ADC stream stopped\r\n> "); }
                else if(u1 >= 50 && u1 <= 1000) { adc_stream_period_ms = u1; last_adc_stream_ms = sys_tick_ms; UART_SendTelemetry("ADC stream started: %u ms\r\n> ", u1); }
                else { UART_SendStr("err: N must be 0 or 50..1000\r\n> "); }
            }
            else if(strcmp(linebuf, "a?") == 0) { UART_SendTelemetry("@ADC:STATUS:offset_i1=%u:stream=%lu\r\n> ", ADC_GetOffsetI1(), (unsigned long)adc_stream_period_ms); }
            else if(strcmp(linebuf, "c") == 0) { ADC_CalibrateI1_256(); UART_SendTelemetry("@ADC:CAL:offset_i1=%u:offset_i2=%u:offset_ires=%u\r\n> ", ADC_GetOffsetI1(), ADC_GetOffsetI2(), ADC_GetOffsetIres()); }
            else if(strcmp(linebuf, "p?") == 0) {
                uint32_t cr1,ccer,bdtr,cnt; PWM_GetStatus(&cr1,&ccer,&bdtr,&cnt);
                UART_SendTelemetry("@PWM:CR1=%lu:CCER=%lu:BDTR=%lu:CNT=%lu\r\n> ", (unsigned long)cr1,(unsigned long)ccer,(unsigned long)bdtr,(unsigned long)cnt);
            }
            else if(sscanf(linebuf, "p=%u,%u,%u,%u", &u1, &u2, &u3, &u4) >= 3) {
                if(u4 == 0) { u4 = 0x3F; }
                PWM_DebugConfig((uint16_t)u1, (uint16_t)u2, u3, (uint8_t)u4);
                UART_SendTelemetry("@PWM:OK:arr=%u:duty=%u:dt=%u\r\n> ", u1, u2, u3);
            }
            else if(linebuf[0] == '1' && linebuf[1] == '\0') {
                if(PROTECT_IsFault()) UART_SendStr("FAULT! send 'f' to clear\r\n> ");
                else { FOC_Start(); UART_SendStr("FOC started\r\n> "); }
            }
            else if(linebuf[0] == '0' && linebuf[1] == '\0') { FOC_Stop(); UART_SendStr("FOC stopped\r\n> "); }
            else if(linebuf[0] == 'm' && linebuf[1] == '\0') { print_help(); }
            else if(linebuf[0] == 'f' && linebuf[1] == '\0') { PROTECT_Clear(); UART_SendStr("fault cleared\r\n> "); }
            else if(linebuf[0] == 's' && linebuf[1] == '=') {
                int32_t rpm = 0; char trail = '\0';
                int f = sscanf(linebuf + 2, "%ld%c", (long*)&rpm, &trail);
                if(f < 1) UART_SendStr("err: no digits\r\n> ");
                else if(f > 1 && trail != '\0') UART_SendStr("err: trailing chars\r\n> ");
                else if(rpm > 50000 || rpm < -50000) UART_SendStr("err: out of range\r\n> ");
                else { FOC_SetSpeed(rpm); UART_SendTelemetry("speed=%ld rpm\r\n> ", (long)FOC_GetSpeed()); }
            } else if(strcmp(linebuf, "dump") == 0) {
                uint32_t psc, arr, bdtr, cr1, cr2, ccer;
                PWM_DumpRegs(&psc, &arr, &bdtr, &cr1, &cr2, &ccer);
                UART_SendTelemetry("@PWM:DUMP:PSC=%lu:ARR=%lu:BDTR=0x%08lX:CR1=0x%08lX:CR2=0x%08lX:CCER=0x%08lX\r\n> ",
                    (unsigned long)psc, (unsigned long)arr, (unsigned long)bdtr,
                    (unsigned long)cr1, (unsigned long)cr2, (unsigned long)ccer);
            } else if(strcmp(linebuf, "dump8") == 0) {
                uint32_t psc, arr, bdtr, cr1, cr2, ccer;
                PWM_DumpRegs8(&psc, &arr, &bdtr, &cr1, &cr2, &ccer);
                UART_SendTelemetry("@PWM8:DUMP:PSC=%lu:ARR=%lu:BDTR=0x%08lX:CR1=0x%08lX:CR2=0x%08lX:CCER=0x%08lX\r\n> ",
                    (unsigned long)psc, (unsigned long)arr, (unsigned long)bdtr,
                    (unsigned long)cr1, (unsigned long)cr2, (unsigned long)ccer);
            } else if(strcmp(linebuf, "sysinfo") == 0) {
                uint32_t psc, tclk;
                PWM_GetSysInfo(&psc, &tclk);
                UART_SendTelemetry("@SYS:CLK=%lu:PSC=%lu:TCLK=%lu:PLLCFGR=0x%08lx:OVR=%lu\r\n> ",
                    (unsigned long)SystemCoreClock, (unsigned long)psc, (unsigned long)tclk,
                    (unsigned long)RCC->PLLCFGR, (unsigned long)ADC_GetOvrCount());
            } else if(sscanf(linebuf, "pp=%u", &u1) == 1) {
                if(u1 < 1 || u1 > 24) UART_SendStr("err: pole pairs must be 1..24\r\n> ");
                else { FOC_SetPolePairs((uint8_t)u1); UART_SendTelemetry("pole_pairs=%u\r\n> ", u1); }
            } else if(sscanf(linebuf, "dt=%u", &u1) == 1) {
                if(u1 > 12700) UART_SendStr("err: max 12700 ns\r\n> ");
                else {
                    PWM_SetDeadTime_ns(u1);
                    UART_SendTelemetry("@PWM:DT=%u ns (DTG=%lu)\r\n> ", u1, (unsigned long)(TIM1->BDTR & 0xFF));
                }
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
                    UART_SendTelemetry("@MP:OK:Rs=%d:Ls=%d:Rr=%d:Lm=%d:Tr=%d:Ke=%d:p=%d:J=%d:AP=1\r\n> ", a1,a2,a3,a4,a5,a6,a7,a8);
                } else {
                    UART_SendTelemetry("@MP:ERROR:%d\r\n> ", _rc);
                }
            } else if(strcmp(linebuf, "piapply") == 0) {
                int32_t _kp, _ki;
                if(Autotune_GetLastPI(&_kp, &_ki) == 0) {
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
            UART_SendTelemetry("@FOC:I1=%ld:I2=%ld:Ires=%ld:VBUS=%ld\r\n", ADC_GetI1_mA(), ADC_GetI2_mA(), ADC_GetIres_mA(), ADC_GetVbus_mV());
        }
    }
}
