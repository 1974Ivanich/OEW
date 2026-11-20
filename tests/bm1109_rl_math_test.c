#include "bm1109_rl_math.h"

#include <assert.h>
#include <stdio.h>

static void test_rs_fit(void)
{
    /* V[mV] = 250[mOhm] * I[mA] / 1000 + 37[mV]. */
    const BM1109_RsSample s[] = {
        { 287, 1000 }, { 537, 2000 }, { 787, 3000 }, { 1037, 4000 }
    };
    BM1109_RsResult r;
    assert(BM1109_RsFit(s, 4u, &r) == 0);
    assert(r.resistance_mOhm == 250);
    assert(r.voltage_offset_mV == 37);
    assert(r.current_span_mA == 3000u);
}

static void test_ls_step(void)
{
    /* R=200mOhm, L=1000uH, I rises 0->1000mA in 1000us.
       V = R*I + L*dI/dt = 0..200mV + 1000mV. */
    const BM1109_LsSample s[] = {
        { 0u, 1000, 0 },
        { 500u, 1100, 500 },
        { 1000u, 1200, 1000 }
    };
    BM1109_LsResult r;
    assert(BM1109_LsFromStep(s, 3u, 200, &r) == 0);
    assert(r.inductance_uH == 1000);
    assert(r.delta_current_mA == 1000);
    assert(r.duration_us == 1000u);
}

static void test_reject_invalid(void)
{
    const BM1109_RsSample rs[] = { { 100, 100 }, { 200, 100 }, { 300, 100 } };
    const BM1109_LsSample ls[] = { { 0u, 100, 0 }, { 0u, 200, 100 } };
    BM1109_RsResult rr;
    BM1109_LsResult lr;
    assert(BM1109_RsFit(rs, 3u, &rr) < 0);
    assert(BM1109_LsFromStep(ls, 2u, 100, &lr) < 0);
    assert(BM1109_RsFit(0, 3u, &rr) < 0);
}

int main(void)
{
    test_rs_fit();
    test_ls_step();
    test_reject_invalid();
    puts("BM1109 R/L math: ALL PASS");
    return 0;
}
