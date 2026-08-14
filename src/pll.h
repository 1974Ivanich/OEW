#ifndef PLL_H
#define PLL_H

#include <stdint.h>

/* FOC_OMEGA_PER_ERPM = 2^32 / (60 × Fs) = 14317 при Fs=5kHz.
 * Максимальная электрическая скорость: FOC_MAX_RPM × FOC_POLE_PAIRS_MAX
 * = 5000 × 24 = 120000 erpm. omega_max = 120000 × 14317 ≈ 1.718e9
 * — помещается в int32 (max 2.147e9). Запас ~20%. */
#define PLL_MAX_ERPM           120000
#define PLL_OMEGA_MAX_Q31      ((int32_t)((int64_t)PLL_MAX_ERPM * 14317))
#define PLL_INTEGRATOR_MAX     ((int32_t)((int64_t)PLL_OMEGA_MAX_Q31 * 90 / 100))

/* Минимальный модуль EMF (Q15) для работы PLL. Ниже — шум: omega/theta
 * не обновляются, lost_cycles растёт; после PLL_LOST_CYCLES подряд —
 * UNLOCKED (omega/integrator = 0, valid = 0) — ревью PLL-03. */
#define PLL_EMF_MIN_Q15        50
#define PLL_LOST_CYCLES        100   /* 100 циклов = 20 мс без EMF → UNLOCKED */

#ifndef CLAMP
#define CLAMP(x, lo, hi) (((x) < (lo)) ? (lo) : (((x) > (hi)) ? (hi) : (x)))
#endif

/* PLL жёстко рассчитан на Fs = 5 кГц (200 мкс): единицы omega_q31 —
 * Δθ q31 за ОДИН цикл (FOC_OMEGA_PER_ERPM = 14317 = 1 eRPM). ts_us намеренно
 * отсутствует (ревью PLL-02): коэффициенты задаются в этих единицах. */
typedef struct {
    int32_t kp, ki;
    uint32_t theta_u32;   /* угол q31 в uint32 — wrap-around определён стандартом */
    int32_t omega_q31;    /* скорость: Δθ (q31) за один FOC-цикл */
    int32_t integrator;
    uint8_t  valid;       /* 1 = PLL сопровождает EMF (ревью PLL-04) */
    uint16_t lost_cycles; /* циклов подряд без валидной EMF */
} PLL;

void PLL_Init(PLL *pll, int32_t kp, int32_t ki);
void PLL_Preset(PLL *pll, int32_t theta_q31, int32_t omega_q31);
void PLL_Update(PLL *pll, int32_t emf_alpha, int32_t emf_beta);
int32_t PLL_GetTheta(PLL *pll);       /* EMF angle (для диагностики) */
int32_t PLL_GetFluxTheta(PLL *pll);   /* rotor flux angle = EMF ∓ π/2.
                                         НЕ используется в FOC_Run — угол Park
                                         берётся из phase accumulator (foc.c).
                                         Пункт ревью Gemini 1.1/1.2: скачок ±180°
                                         при осцилляции знака omega — не влияет,
                                         т.к. вызовов нет. */
int32_t PLL_GetSpeed(PLL *pll);
uint8_t PLL_IsValid(PLL *pll);   /* 1 = недавно была валидная EMF (PLL-04) */

#endif
