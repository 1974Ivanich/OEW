#include "observer.h"
#include "pll.h"
#include "flux_weakening.h"
#include <stdio.h>

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
    BEMFObserver observer;
    BEMF_Init(&observer, 100, 1000, 200, 24000);
    BEMF_Update(&observer, 12000, 0, 100, 0);
    BEMF_Update(&observer, 12000, 0, 200, 20);
    check("BEMF produces nonzero estimate", BEMF_GetMagnitude(&observer) > 0);
    check("BEMF valid for clean signal", BEMF_IsValid(&observer) != 0);

    BEMF_Init(&observer, 100, 1000, 200, 24000);
    BEMF_Update(&observer, 0, 0, 0, 0);
    check("zero input first sample invalid", BEMF_IsValid(&observer) == 0);

    PLL pll;
    PLL_Init(&pll, 1000, 10);
    PLL_Preset(&pll, 0, 14317 * 1000);
    for (int i = 0; i < 20; ++i) PLL_Update(&pll, 0, 10000);
    check("PLL locks on valid EMF", PLL_IsValid(&pll) != 0);
    check("PLL speed bounded", PLL_GetSpeed(&pll) <= PLL_OMEGA_MAX_Q31);
    for (int i = 0; i <= PLL_LOST_CYCLES; ++i) PLL_Update(&pll, 0, 0);
    check("PLL unlocks after lost cycles", PLL_IsValid(&pll) == 0);
    check("PLL clears speed after loss", PLL_GetSpeed(&pll) == 0);

    FluxWeakening fw;
    FW_Init(&fw, 2000, 100);
    FW_SetVmaxQ15(&fw, 30000);
    FW_SetBaseSpeedRpm(&fw, 1000);
    FW_Update(&fw, 10000, 10000, 20000, 3000, 500);
    check("FW inactive below speed gate", !FW_IsActive(&fw));
    FW_Update(&fw, 20000, 10000, 20000, 3000, 1000);
    check("FW active at base speed under saturation", FW_IsActive(&fw));
    check("FW Id bounded by base current", FW_GetIdAdd(&fw) >= -3000);
    check("FW Iq limit bounded", FW_GetIqLimit(&fw) >= 0 && FW_GetIqLimit(&fw) <= 30000);
    FW_Update(&fw, 10000, 10000, 32767, 3000, 500);
    check("FW returns inactive below hysteresis", !FW_IsActive(&fw));

    printf("Observer/PLL/FW: %d checks, %d failures\n", checks, failures);
    return failures != 0;
}
