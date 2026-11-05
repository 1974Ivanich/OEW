/* Hosted-тест диагностического DAC-моста (src/dac_debug.c).
 *
 * Проверяется то, что можно проверить без осциллографа: масштабирование сигналов
 * в 12 бит (насыщение, середина шкалы), разбор имён, запись в DHR12RD и no-op,
 * когда оба канала выключены. Регистры DAC/GPIO заменены структурами в памяти.
 */
#include <stdio.h>
#include <stdint.h>
#include "stm32g474xx.h"

DAC_TypeDef mock_dac1;
GPIO_TypeDef mock_gpioa;
static RCC_TypeDef mock_rcc;

/* dac_debug.c обращается к RCC/DAC1/GPIOA — подменяем на структуры в памяти
 * (DAC1 и GPIOA уже заданы в tests/mocks/stm32g474xx.h). */
#ifdef RCC
#undef RCC
#endif
#define RCC (&mock_rcc)

/* Заглушки геттеров, которые вызывает DacDebug_Update(). */
static int32_t stub_theta = 0;
static int32_t stub_id = 0;
static int32_t stub_iq = 0;
static int32_t stub_vbus = 0;
static int32_t stub_speed = 0;
int32_t FOC_GetThetaMilliRad(void) { return stub_theta; }
int32_t FOC_GetIdMeasured_mA(void) { return stub_id; }
int32_t FOC_GetIqMeasured_mA(void) { return stub_iq; }
int32_t FOC_GetMeasSpeedRPM(void) { return stub_speed; }
int32_t ADC_GetVbus_mV(void) { return stub_vbus; }

#include "../src/dac_debug.c"

static int failures;
static int checks;

static void check(const char *name, int condition)
{
    ++checks;
    if (!condition) {
        ++failures;
        printf("FAIL: %s\n", name);
    } else {
        printf("ok:   %s\n", name);
    }
}

int main(void)
{
    /* Однополярные шкалы. */
    check("theta 0 -> 0", DacDebug_Scale(DAC_SIG_THETA, 0) == 0u);
    check("theta max -> full scale", DacDebug_Scale(DAC_SIG_THETA, 6283) == 4095u);
    check("theta over-range saturates", DacDebug_Scale(DAC_SIG_THETA, 9999) == 4095u);
    check("theta negative clamps to 0", DacDebug_Scale(DAC_SIG_THETA, -5) == 0u);
    check("vbus 30V -> half scale", DacDebug_Scale(DAC_SIG_VBUS, 30000) == 2047u);
    check("vbus 60V -> full scale", DacDebug_Scale(DAC_SIG_VBUS, 60000) == 4095u);

    /* Двуполярные шкалы: 2048 = ноль. */
    check("id 0 -> mid", DacDebug_Scale(DAC_SIG_ID, 0) == 2047u);
    check("iq 0 -> mid", DacDebug_Scale(DAC_SIG_IQ, 0) == 2047u);
    check("id +10A -> full", DacDebug_Scale(DAC_SIG_ID, 10000) == 4095u);
    check("id -10A -> 0", DacDebug_Scale(DAC_SIG_ID, -10000) == 0u);
    check("speed +3000 -> full", DacDebug_Scale(DAC_SIG_SPEED, 3000) == 4095u);
    check("speed -3000 -> 0", DacDebug_Scale(DAC_SIG_SPEED, -3000) == 0u);
    check("off -> 0", DacDebug_Scale(DAC_SIG_OFF, 1234) == 0u);

    /* Имена сигналов и разбор строки. */
    {
        DacSignal s = DAC_SIG_COUNT;
        check("name theta", DacDebug_SignalFromName("theta", &s) == 0 && s == DAC_SIG_THETA);
        check("name id", DacDebug_SignalFromName("id", &s) == 0 && s == DAC_SIG_ID);
        check("name iq", DacDebug_SignalFromName("iq", &s) == 0 && s == DAC_SIG_IQ);
        check("name vbus", DacDebug_SignalFromName("vbus", &s) == 0 && s == DAC_SIG_VBUS);
        check("name speed", DacDebug_SignalFromName("speed", &s) == 0 && s == DAC_SIG_SPEED);
        check("name off", DacDebug_SignalFromName("off", &s) == 0 && s == DAC_SIG_OFF);
        check("unknown name rejected", DacDebug_SignalFromName("bogus", &s) == -2);
        check("null name rejected", DacDebug_SignalFromName(0, &s) == -1);
    }

    /* Init: тактирование, analog-режим PA4/PA5, включение каналов. */
    mock_dac1.CR = 0xFFFFFFFFu;
    mock_gpioa.MODER = 0xFFFFFFFFu;
    DacDebug_Init();
    check("init sets DAC1 clock", (mock_rcc.AHB2ENR & (RCC_AHB2ENR_DAC1EN | RCC_AHB2ENR_GPIOAEN)) ==
                                  (RCC_AHB2ENR_DAC1EN | RCC_AHB2ENR_GPIOAEN));
    check("init enables both channels", mock_dac1.CR == (DAC_CR_EN1 | DAC_CR_EN2));
    check("init sets PA4/PA5 analog", ((mock_gpioa.MODER >> 8) & 0xFu) == 0xFu);
    check("init zeroes output", mock_dac1.DHR12RD == 0u);

    /* Update: пока оба OFF — регистр не трогаем. */
    DacDebug_SetSignals(DAC_SIG_OFF, DAC_SIG_OFF);
    check("off/off is not active", DacDebug_IsActive() == 0u);
    mock_dac1.DHR12RD = 0xDEADBEEFu;
    DacDebug_Update();
    check("update is no-op when both off", mock_dac1.DHR12RD == 0xDEADBEEFu);

    /* Update: theta на CH1, id на CH2, оба попадают в dual-регистр. */
    stub_theta = 3142;   /* половина оборота */
    stub_id = -5000;     /* половина отрицательной шкалы */
    DacDebug_SetSignals(DAC_SIG_THETA, DAC_SIG_ID);
    DacDebug_Update();
    {
        const uint32_t ch1 = mock_dac1.DHR12RD & 0x0FFFu;
        const uint32_t ch2 = (mock_dac1.DHR12RD >> 16) & 0x0FFFu;
        check("ch1 = theta/2", ch1 == 2047u);
        check("ch2 = -5A -> quarter", ch2 == 1023u);
    }

    /* OFF на канале означает 0 на его выходе. */
    DacDebug_SetSignals(DAC_SIG_OFF, DAC_SIG_VBUS);
    stub_vbus = 60000;
    DacDebug_Update();
    check("ch1 off -> 0", (mock_dac1.DHR12RD & 0x0FFFu) == 0u);
    check("ch2 vbus full", ((mock_dac1.DHR12RD >> 16) & 0x0FFFu) == 4095u);

    printf("DAC debug: %d checks, %d failures\n", checks, failures);
    return failures != 0;
}
