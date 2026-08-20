#include "vf_start.h"
#include <stdio.h>
#include <stdint.h>

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
    VFStart vf;
    VF_Init(&vf, 6000, 10);
    check("init not complete", !VF_IsComplete(&vf));
    check("init speed zero", VF_GetSpeed(&vf) == 0);

    for (int i = 0; i < 60; ++i) VF_Update(&vf);
    check("ramp reaches target", VF_GetSpeed(&vf) == 6000);
    check("ramp completes", VF_IsComplete(&vf));

    int32_t theta_before = VF_GetTheta(&vf);
    VF_Update(&vf);
    check("complete keeps integrating angle", VF_GetTheta(&vf) != theta_before);
    check("complete keeps target speed", VF_GetSpeed(&vf) == 6000);

    VF_SetTarget(&vf, 3000);
    check("target change reopens ramp", !VF_IsComplete(&vf));
    for (int i = 0; i < 60; ++i) VF_Update(&vf);
    check("new target reached", VF_GetSpeed(&vf) == 3000);
    check("new ramp completes", VF_IsComplete(&vf));

    VF_Init(&vf, -6000, 1);
    for (int i = 0; i < 10; ++i) VF_Update(&vf);
    check("negative target reached", VF_GetSpeed(&vf) == -6000);
    check("negative ramp completes", VF_IsComplete(&vf));
    check("theta is modular signed value", (uint32_t)VF_GetTheta(&vf) != 0U);

    VF_SetTarget(&vf, 0);
    check("zero target resets ramp speed", VF_GetSpeed(&vf) == 0);
    check("zero target reopens ramp", !VF_IsComplete(&vf));

    printf("V/f start: %d checks, %d failures\n", checks, failures);
    return failures != 0;
}
