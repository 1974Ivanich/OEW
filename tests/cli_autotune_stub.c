/* Заглушки для cli_test: cli.c вызывает autotune-команды hold=/pv=,
 * но тест линкует cli.c без autotune.c. */
#include <stdint.h>

int8_t Autotune_HoldVector(uint16_t theta_deg, int32_t hold_ms)
{
    (void)theta_deg; (void)hold_ms;
    return -9;
}

int8_t Autotune_ProbeDuty(uint16_t duty_pct, int32_t hold_ms)
{
    (void)duty_pct; (void)hold_ms;
    return -9;
}
