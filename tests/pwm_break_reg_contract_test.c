/* Контракт регистров BKIN (RM0440, STM32G474).
 *
 * Цифровой фильтр внешнего входа BKIN — это поле BKF[3:0] в TIMx_BDTR[19:16].
 * В TIMx_AF1 поля фильтра нет: там только BKINE (бит 0), BKINP (бит 9),
 * BKCMPxE/BKCMPxP и ETRSEL, а биты 12..15 заняты BKCMP3P | BKCMP4P |
 * ETRSEL[1:0] — настройка break их писать не должна.
 *
 * Тест фиксирует это как контракт. Он падает в двух случаях, которые раньше
 * не ловил никто: маска фильтра ушла в AF1, либо фильтр не оказался в BDTR.
 * Ожидаемое значение фильтра — предмет отдельного решения о допустимой
 * задержке аппаратной защиты; менять здесь вместе с правкой src/pwm.c.
 *
 * Коды выхода: 0 — контракт выполнен, 1 — нарушен.
 */

#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "pwm.h"
#include "stm32g474xx.h"

TIM_TypeDef host_tim1;
TIM_TypeDef host_tim8;
GPIO_TypeDef host_gpioa;
GPIO_TypeDef host_gpiob;
GPIO_TypeDef host_gpioc;
GPIO_TypeDef host_gpiod;
RCC_TypeDef host_rcc;
uint32_t SystemCoreClock = 170000000u;
volatile uint8_t g_clock_fail;

bool ADC_InjectedIsArmed(void) { return false; }
void ADC_InjectedStop(void) { }
void ADC_SetExpectedWindow(uint8_t sector, uint8_t window, bool valid)
{
    (void)sector;
    (void)window;
    (void)valid;
}
void ADC_SetControlAdmission(bool enabled) { (void)enabled; }
int PROTECT_IsFault(void) { return 0; }

/* AF1[15:12] = BKCMP3P | BKCMP4P | ETRSEL[1:0] — не поле фильтра BKIN. */
#define AF1_NOT_BKF_Msk (0xFu << 12)

/* Намерение кода, зафиксированное вместе с дефектом: BKF=0b1111 — fDTS/32,
 * N=8, около 1.5 мкс при 170 МГц. Это НЕ решение о допустимой задержке
 * аппаратной защиты: при пересмотре фильтра значение меняется здесь и в
 * src/pwm.c одновременно. */
#define BDTR_BKF_INTENDED (0xFu << TIM_BDTR_BKF_Pos)

static unsigned failures;

static void expect(const char *what, uint32_t actual, uint32_t expected)
{
    if (actual == expected) {
        printf("ok   %-28s 0x%08lX\n", what, (unsigned long)actual);
        return;
    }
    printf("FAIL %-28s получено 0x%08lX, ожидалось 0x%08lX\n", what,
           (unsigned long)actual, (unsigned long)expected);
    failures++;
}

int main(void)
{
    memset(&host_tim1, 0, sizeof(host_tim1));
    memset(&host_tim8, 0, sizeof(host_tim8));
    memset(&host_gpioa, 0, sizeof(host_gpioa));
    memset(&host_gpiob, 0, sizeof(host_gpiob));
    memset(&host_gpioc, 0, sizeof(host_gpioc));
    memset(&host_gpiod, 0, sizeof(host_gpiod));
    memset(&host_rcc, 0, sizeof(host_rcc));

    PWM_Init();

    puts("--- контракт регистров BKIN (TIM1/TIM8) ---");

    /* 1. Поля AF1, похожие на поле фильтра, настройка break не пишет. */
    expect("TIM1.AF1[15:12] свободны", host_tim1.AF1 & AF1_NOT_BKF_Msk, 0u);
    expect("TIM8.AF1[15:12] свободны", host_tim8.AF1 & AF1_NOT_BKF_Msk, 0u);

    /* 2. Фильтр обязан стоять в BDTR[19:16] — единственном поле BKF. */
    expect("TIM1.BDTR BKF[3:0]", host_tim1.BDTR & TIM_BDTR_BKF_Msk,
           BDTR_BKF_INTENDED);
    expect("TIM8.BDTR BKF[3:0]", host_tim8.BDTR & TIM_BDTR_BKF_Msk,
           BDTR_BKF_INTENDED);

    /* 3. Полярность и запреты не разъехались вместе с фильтром. */
    expect("TIM1.AF1 BKINE", host_tim1.AF1 & (1u << 0), 1u << 0);
    expect("TIM8.AF1 BKINE", host_tim8.AF1 & (1u << 0), 1u << 0);
    expect("TIM1.AF1 BKINP clear", host_tim1.AF1 & (1u << 9), 0u);
    expect("TIM8.AF1 BKINP clear", host_tim8.AF1 & (1u << 9), 0u);
    expect("TIM1.BDTR BKE", host_tim1.BDTR & TIM_BDTR_BKE, TIM_BDTR_BKE);
    expect("TIM8.BDTR BKE", host_tim8.BDTR & TIM_BDTR_BKE, TIM_BDTR_BKE);
    expect("TIM1.BDTR BKP/BK2E/AOE clear",
           host_tim1.BDTR & (TIM_BDTR_BKP | TIM_BDTR_BK2E | TIM_BDTR_AOE), 0u);
    expect("TIM8.BDTR BKP/BK2E/AOE clear",
           host_tim8.BDTR & (TIM_BDTR_BKP | TIM_BDTR_BK2E | TIM_BDTR_AOE), 0u);

    if (failures != 0u) {
        printf("pwm_break_reg_contract_test: FAIL (%u нарушений контракта BKIN)\n",
               failures);
        return 1;
    }
    puts("pwm_break_reg_contract_test: PASS");
    return 0;
}
