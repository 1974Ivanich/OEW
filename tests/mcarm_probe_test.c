/* QEMU probe: does the arm-compiled board BuildRequest accept delta 0..47?
 * semihosting output, same style as foc_math_test.c QEMU branch. */
#include <stdint.h>
#include "map_capture_profiles.h"
#include "map_capture.h"

static void sh_puts(const char *s) {
    register int r0 __asm__("r0") = 0x04;
    register const char *r1 __asm__("r1") = s;
    __asm__ volatile("bkpt 0xAB" : "+r"(r0) : "r"(r1) : "memory");
}
static void sh_putu(uint32_t v) {
    char buf[11]; int i = 10; buf[i] = 0;
    do { buf[--i] = (char)('0' + (v % 10u)); v /= 10u; } while (v);
    sh_puts(&buf[i]);
}

int main(void)
{
    MapCaptureRequest r;
    uint32_t i, ok = 0;
    for (i = 0; i < 48u; ++i) {
        if (MapCaptureProfile_BuildRequest(0x424F4152u + i, 1u, &r)) ++ok;
    }
    sh_puts("PROBE ok=");
    sh_putu(ok);
    sh_puts(" single0=");
    sh_putu(MapCaptureProfile_BuildRequest(0x424F4152u, 1u, &r) ? 1u : 0u);
    sh_puts("\n");
    for (;;) { }
}
