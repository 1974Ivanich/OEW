#include "encoder.h"
#include "stm32g474xx.h"
#include <stdio.h>

void TIM2_IRQHandler(void);

static int failures;
static int checks;
static void check(const char *name, int condition)
{
    ++checks;
    if (!condition) { ++failures; printf("FAIL: %s\n", name); }
    else printf("ok:   %s\n", name);
}

static void capture(uint32_t period_us, uint32_t pulse_us)
{
    TIM2->CCR1 = period_us;
    TIM2->CCR2 = pulse_us;
    TIM2->SR = TIM_SR_CC1IF;
    TIM2_IRQHandler();
    ENC_Update();
}

int main(void)
{
    ENC_Init();
    check("initial timeout", ENC_GetError() == ENC_ERR_TIMEOUT);

    capture(1087, 0);
    check("period about 920Hz", ENC_GetPeriod_us() == 1087);
    check("low angle", ENC_GetAngle14() < 100);
    check("low angle degrees", ENC_GetAngle_deg() >= 0 && ENC_GetAngle_deg() < 5);

    capture(1087, 1084);
    check("high angle", ENC_GetAngle14() > 16300);
    check("high angle degrees", ENC_GetAngle_deg() > 350 && ENC_GetAngle_deg() <= 360);

    capture(1087, 543);
    check("mid angle", ENC_GetAngle14() > 7000 && ENC_GetAngle14() < 9000);
    check("speed finite", ENC_GetSpeed_rpm() > -100000 && ENC_GetSpeed_rpm() < 100000);

    TIM2->CCR1 = 100;
    TIM2->CCR2 = 50;
    TIM2->SR = TIM_SR_CC1IF;
    TIM2_IRQHandler();
    check("bad period error", ENC_GetError() == ENC_ERR_BAD_PERIOD);
    check("bad period angle invalid", ENC_GetAngle14() == 0xFFFFu);

    ENC_Init();
    for (int i = 0; i < 6; ++i) ENC_Update();
    check("missing capture timeout", ENC_GetError() == ENC_ERR_TIMEOUT);
    check("timeout angle invalid", ENC_GetAngle14() == 0xFFFFu);

    printf("Encoder: %d checks, %d failures\n", checks, failures);
    return failures != 0;
}
