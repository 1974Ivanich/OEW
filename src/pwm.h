#ifndef PWM_H
#define PWM_H

#include <stdbool.h>
#include <stdint.h>

/* The context identifies the PWM state that will become active on the next
 * TIM1 update event / TRGO. Its indices are consumed by AdcFrame and
 * Current_Reconstruct. `valid` means that this exact state has two independent,
 * scope-verified DC-link current equations; it is not a generic «ADC enabled»
 * flag. */
typedef struct {
    uint8_t sector;       /* 0..5: measured reconstruction-map sector */
    uint8_t window;       /* 0..1: measured sampling window within sector */
    bool valid;           /* false for every uncharacterised/saturated state */
} PwmSampleContext;

#define PWM_ENABLE_OK                 0
#define PWM_ENABLE_FAULT_LATCHED     -1
#define PWM_ENABLE_CLOCK_FAILED      -2
#define PWM_ENABLE_CONTEXT_INVALID   -3

void PWM_Init(void);

/* Normal FOC control API. It writes both OEW inverter preloads and publishes
 * the context before the next TIM1 update/TRGO can sample those preloads.
 * Returns false and invalidates the context on bad indices or NULL context.
 * `context->valid` is allowed false; the subsequent AdcFrame is intentionally
 * invalid and normal PWM_Enable() will refuse to arm. */
bool PWM_SetControlVector(int16_t mu, int16_t mv, int16_t mw,
                          const PwmSampleContext *context);

/* Returns the context paired with the next ADC trigger. This read is for
 * diagnostics only; AdcFrame is the authoritative per-sample record. */
bool PWM_GetPendingSampleContext(PwmSampleContext *out);
bool PWM_HasValidSampleContext(void);

/* Explicitly invalidates both local context and ADC expected window. Call this
 * before any service pattern, direct CCR modification or mapping transition. */
void PWM_InvalidateSampleContext(void);

/* Q15 modulation compatibility APIs. They remain for service/backward source
 * compatibility but intentionally invalidate control context. They must not be
 * used by FOC/Vf control. */
void PWM_SetMod1(int16_t mu, int16_t mv, int16_t mw);
void PWM_SetMod2(int16_t mu, int16_t mv, int16_t mw);

/* Absolute duty % 0..100. SERVICE ONLY; each call invalidates sample context. */
void PWM_SetDuty1(uint16_t u, uint16_t v, uint16_t w);
void PWM_SetDuty2(uint16_t u, uint16_t v, uint16_t w);

/* Normal power-stage arm. It refuses a latched fault, clock failure or absent /
 * invalid sample context. Call only after ADC injected groups are armed. */
int PWM_Enable(void);

/* Service-only arm for map commissioning. Publishes the supplied diagnostic
 * context (frame stays MAPPING_UNVERIFIED while control admission is false)
 * and opens gates with the same order as PWM_Enable(). Caller must guarantee
 * bounded energy and unconditional PWM_Disable() afterwards. */
int PWM_ServiceEnable(const PwmSampleContext *context);

void PWM_Disable(void);
uint32_t PWM_IsEnabled(void);
void PWM_SetDeadTimeComp(int32_t dt_ticks);

/* Debug/service only. It invalidates sampling context and never authorises FOC. */
void PWM_DebugSetModulation(uint16_t arr, uint16_t mod_pct,
                            uint32_t dt_ns, uint8_t mask);
uint16_t PWM_GetARR(void);
int PWM_SetDeadTime_ns(uint32_t dt_ns);
uint32_t PWM_GetDeadTime_ns(void);
void PWM_GetStatus(uint32_t *cr1, uint32_t *ccer, uint32_t *bdtr, uint32_t *cnt);
void PWM_GetSysInfo(uint32_t *psc, uint32_t *tclk);
void PWM_DumpRegs8(uint32_t *psc, uint32_t *arr, uint32_t *bdtr,
                   uint32_t *cr1, uint32_t *cr2, uint32_t *ccer);
void PWM_DumpRegs(uint32_t *psc, uint32_t *arr, uint32_t *bdtr,
                  uint32_t *cr1, uint32_t *cr2, uint32_t *ccer);

#endif /* PWM_H */
