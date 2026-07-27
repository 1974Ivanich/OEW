#include "stm32g474xx.h"
#include "pwm.h"
#include "adc.h"
#include "uart.h"
#include "cordic_math.h"
#include "observer.h"
#include "pll.h"
#include "foc.h"
#include "protect.h"

/* ── SysTick: миллисекундный таймер для телеметрии ─────────────────────── */
static volatile uint32_t sys_tick_ms = 0;
void SysTick_Handler(void) { sys_tick_ms++; }

static void GPIO_Init(void) {
    RCC->AHB2ENR |= RCC_AHB2ENR_GPIOAEN | RCC_AHB2ENR_GPIOBEN | RCC_AHB2ENR_GPIOCEN;

    /* TIM1: PC0/PC1/PC2 (AF2) */
    GPIOC->MODER &= ~((3U<<0)|(3U<<2)|(3U<<4));
    GPIOC->MODER |=  (2U<<0)|(2U<<2)|(2U<<4);
    GPIOC->OSPEEDR |= (3U<<0)|(3U<<2)|(3U<<4);   /* Very High Speed — обязательно для ШИМ */
    GPIOC->AFR[0] &= ~((0xF<<0)|(0xF<<4)|(0xF<<8));
    GPIOC->AFR[0] |=  (2U<<0)|(2U<<4)|(2U<<8);

    /* TIM1N: PA7=CH1N, PB0=CH2N, PB1=CH3N (AF6) */
    GPIOA->MODER &= ~(3U<<14); GPIOA->MODER |= (2U<<14);
    GPIOA->OSPEEDR |= (3U<<14);
    GPIOA->AFR[0] &= ~(0xF<<28); GPIOA->AFR[0] |= (6U<<28);
    GPIOB->MODER &= ~((3U<<0)|(3U<<2)); GPIOB->MODER |= (2U<<0)|(2U<<2);
    GPIOB->OSPEEDR |= (3U<<0)|(3U<<2);
    GPIOB->AFR[0] &= ~((0xF<<0)|(0xF<<4)); GPIOB->AFR[0] |= (6U<<0)|(6U<<4);

    /* TIM8: PC6/PC7/PC8 (AF4) */
    GPIOC->MODER &= ~((3U<<12)|(3U<<14)|(3U<<16));
    GPIOC->MODER |=  (2U<<12)|(2U<<14)|(2U<<16);
    GPIOC->OSPEEDR |= (3U<<12)|(3U<<14)|(3U<<16);
    GPIOC->AFR[0] &= ~((0xF<<24)|(0xF<<28));
    GPIOC->AFR[0] |=  (4U<<24)|(4U<<28);
    GPIOC->AFR[1] &= ~(0xF<<0); GPIOC->AFR[1] |= (4U<<0);

    /* TIM8N: PC10/PC11/PC12 (AF4) */
    GPIOC->MODER &= ~((3U<<20)|(3U<<22)|(3U<<24));
    GPIOC->MODER |=  (2U<<20)|(2U<<22)|(2U<<24);
    GPIOC->OSPEEDR |= (3U<<20)|(3U<<22)|(3U<<24);
    GPIOC->AFR[1] &= ~((0xF<<8)|(0xF<<12)|(0xF<<16));
    GPIOC->AFR[1] |=  (4U<<8)|(4U<<12)|(4U<<16);

    /* EN: PB4, PB5 — output, сразу low (выключено), внутренний pull-down
     * на случай наводок до завершения GPIO_Init. */
    GPIOB->MODER &= ~((3U<<8)|(3U<<10)); GPIOB->MODER |= (1U<<8)|(1U<<10);
    GPIOB->OSPEEDR |= (1U<<8)|(1U<<10);   /* Low — достаточно для EN-сигнала */
    GPIOB->PUPDR &= ~((3U<<8)|(3U<<10));  GPIOB->PUPDR |= (2U<<8)|(2U<<10);  /* pull-down */
    GPIOB->BSRR = (1U<<20)|(1U<<21); // OFF (биты сброса)

    /* ADC2 pins: PA0/PA1/PA6/PC4 (analog) */
    GPIOA->MODER |= (3U<<0)|(3U<<2)|(3U<<12);
    GPIOC->MODER |= (3U<<8);
}

/* ── ADC1_2 IRQ: детерминированный цикл FOC ────────────────────────────
 * TIM1 в center-aligned mode генерирует update event на пике счётчика
 * (ARR) → TRGO → ADC2 injected group (hardware trigger) → JEOS → этот ISR.
 * PWM_Init() считает PSC/ARR от SystemCoreClock для целевой 5 кГц → Ts=200 мкс.
 * АЦП сэмплирует строго в одной точке PWM-цикла — нулевой джиттер. */
void ADC1_2_IRQHandler(void) {
    if(ADC2->ISR & ADC_ISR_JEOS) {
        ADC2->ISR = ADC_ISR_JEOS;          /* атомарная очистка JEOS (rc_w1) */
        ADC_ReadInjected();                /* JDR1-4 → adc_data */
        if(FOC_IsRunning()) {
            /* Защита ПЕРЕД FOC_Run — по свежим данным АЦП. При fault
             * PROTECT_Check сам вызывает PWM_Disable; здесь дополнительно
             * останавливаем FOC, чтобы не оставить систему в running. */
            PROTECT_Check();
            if(PROTECT_IsFault()) {
                FOC_Stop();
            } else {
                FOC_Run();
            }
        }
    }
}

int main(void) {
    /* Актуализируем SystemCoreClock по фактическому состоянию RCC —
     * от него считаются UART BRR, PWM PSC/ARR/DTG и SysTick. */
    SystemCoreClockUpdate();

    UART_Init();
    UART_SendStr("OEW FOC v0.2\r\n");

    GPIO_Init();
    UART_SendStr("GPIO OK\r\n");

    ADC_Init();
    UART_SendStr("ADC OK\r\n");

    PWM_Init();
    UART_SendStr("PWM OK\r\n");

    CORDIC_Init();
    UART_SendStr("CORDIC OK\r\n");

    PROTECT_Init();
    UART_SendStr("PROTECT OK\r\n");

    FOC_Init();
    UART_SendStr("FOC init OK\r\n");

    /* ADC injected group: аппаратный запуск от TIM1_TRGO */
    ADC_InjectedInit();
    UART_SendStr("ADC injected OK\r\n");

    /* SysTick: 1 мс при 16 МГц HSI */
    SysTick_Config(SystemCoreClock / 1000U);

    /* NVIC: ADC1_2 — высший приоритет (control loop, triggered by TIM1_TRGO) */
    NVIC_SetPriority(ADC1_2_IRQn, 0);
    NVIC_EnableIRQ(ADC1_2_IRQn);

    UART_SendStr("Ready. Commands: 1=start, 0=stop, s=500=speed, p=4=pole pairs, m=menu\r\n");
    UART_SendStr("> ");

    uint32_t last_telem_ms = 0;

    while(1) {
        /* Обработка UART команд (строковый формат) */
        char linebuf[16];
        int rc = UART_ReadLine(linebuf, sizeof(linebuf));
        if(rc > 0) {
            /* Одиночные символы: '1' start, '0' stop, 'm' menu, 'c' clear fault */
            if(linebuf[0] == '1' && linebuf[1] == '\0') {
                if(PROTECT_IsFault()) {
                    UART_SendStr("FAULT! send 'c' to clear\r\n> ");
                } else {
                    FOC_Start();
                    UART_SendStr("FOC started\r\n> ");
                }
            } else if(linebuf[0] == '0' && linebuf[1] == '\0') {
                FOC_Stop();
                UART_SendStr("FOC stopped\r\n> ");
            } else if(linebuf[0] == 'm' && linebuf[1] == '\0') {
                UART_SendStr("1=start 0=stop s=500=spd p=4=poles m=menu c=clear\r\n> ");
            } else if(linebuf[0] == 'c' && linebuf[1] == '\0') {
                PROTECT_Clear();
                UART_SendStr("fault cleared\r\n> ");
            } else if(linebuf[0] == 's' && linebuf[1] == '=') {
                /* s=500 — задать скорость в об/мин */
                int32_t rpm = 0;
                int i = 2;
                int sign = 1;
                int digits = 0;
                int overflow = 0;
                if(linebuf[i] == '-') { sign = -1; i++; }
                while(linebuf[i] >= '0' && linebuf[i] <= '9') {
                    if(rpm > 99999) overflow = 1;
                    if(!overflow) rpm = rpm * 10 + (linebuf[i] - '0');
                    i++;
                    digits++;
                }
                if(digits == 0) {
                    UART_SendStr("err: no digits\r\n> ");
                } else if(linebuf[i] != '\0') {
                    UART_SendStr("err: trailing chars\r\n> ");
                } else if(overflow) {
                    UART_SendStr("err: value too large\r\n> ");
                } else {
                    rpm *= sign;
                    FOC_SetSpeed(rpm);
                    int32_t actual = FOC_GetSpeed();
                    UART_SendTelemetry("speed=%ld rpm (clamped ±5000)\r\n> ", (long)actual);
                }
            } else if(linebuf[0] == 'p' && linebuf[1] == '=') {
                /* p=4 — задать число пар полюсов (только при остановленном FOC) */
                int32_t pp = 0;
                int i = 2;
                int digits = 0;
                while(linebuf[i] >= '0' && linebuf[i] <= '9') {
                    pp = pp * 10 + (linebuf[i] - '0');
                    i++;
                    digits++;
                }
                if(digits == 0) {
                    UART_SendStr("err: no digits\r\n> ");
                } else if(linebuf[i] != '\0') {
                    UART_SendStr("err: trailing chars\r\n> ");
                } else if(FOC_SetPolePairs(pp) == 0) {
                    UART_SendTelemetry("pole pairs=%ld\r\n> ", (long)pp);
                } else {
                    UART_SendStr("p err: stop FOC first, range 1..24\r\n> ");
                }
            } else {
                UART_SendStr("unknown\r\n> ");
            }
        } else if(rc < 0) {
            UART_SendStr("line overflow\r\n> ");
        }

        /* Телеметрия по SysTick — каждые 100 мс, не зависит от FOC-цикла */
        if((sys_tick_ms - last_telem_ms) >= 100) {
            last_telem_ms = sys_tick_ms;
            UART_SendTelemetry("@FOC:I1=%ld:I2=%ld:IN=%ld:VBUS=%ld\n",
                ADC_GetI1_mA(), ADC_GetI2_mA(), ADC_GetIN_mA(), ADC_GetVbus_mV());
        }
    }
}
