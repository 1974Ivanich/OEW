#include "pll.h"
#include "cordic_math.h"

void PLL_Init(PLL *pll, int32_t kp, int32_t ki, int32_t ts_us) {
    pll->kp = kp;
    pll->ki = ki;
    pll->ts_us = ts_us;
    pll->theta_u32 = 0;
    pll->omega_q31 = 0;
    pll->integrator = 0;
}

/* Предустановка состояния — для бесшовного перехода с V/f открытого цикла:
 * theta и omega (в единицах Δθ q31 за цикл) берутся из open-loop генератора,
 * интегратор заряжается так, чтобы при err=0 скорость сохранялась. */
void PLL_Preset(PLL *pll, int32_t theta_q31, int32_t omega_q31) {
    pll->theta_u32 = (uint32_t)theta_q31;
    pll->omega_q31 = omega_q31;
    pll->integrator = omega_q31;
}

void PLL_Update(PLL *pll, int32_t emf_alpha, int32_t emf_beta) {
    /* Нормализуем EMF */
    int32_t mod, angle;
    CORDIC_Modulus(emf_alpha, emf_beta, &mod, &angle);
    if(mod == 0) return;

    int32_t e_norm_a = (int32_t)(((int64_t)emf_alpha << 15) / mod);
    int32_t e_norm_b = (int32_t)(((int64_t)emf_beta  << 15) / mod);

    /* Ошибка PLL: err = -Eα*sin(θ) + Eβ*cos(θ)
     * Q15×Q15 = Q30; сумма двух Q30 может превысить int32 — считаем в int64 */
    int32_t s, c;
    CORDIC_SinCos((int32_t)pll->theta_u32, &s, &c);
    int32_t err = (int32_t)((-(int64_t)e_norm_a * s + (int64_t)e_norm_b * c) >> 15);

    /* PI. Единицы omega: приращение угла q31 за один FOC-цикл (Ts).
     * Прямое интегрирование theta += omega без умножения на ts_us —
     * исключает переполнение int32 на высоких скоростях. */
    /* Anti-windup: ограничение интегратора (предотвращает раскрутку
     * при потере захвата / шуме EMF на малых оборотах). */
    pll->integrator += (int32_t)(((int64_t)pll->ki * err) >> 15);
    if(pll->integrator > PLL_INTEGRATOR_MAX) pll->integrator = PLL_INTEGRATOR_MAX;
    if(pll->integrator < -PLL_INTEGRATOR_MAX) pll->integrator = -PLL_INTEGRATOR_MAX;
    int32_t omega = ((pll->kp * err) >> 15) + pll->integrator;
    pll->omega_q31 = omega;

    /* Интегрирование угла. Переполнение uint32_t определено стандартом C
     * как wrap-around — корректный модуль 2π в формате q31.
     * Приведение (uint32_t)omega НАМЕРЕННОЕ: отрицательная omega (реверс)
     * через дополнение до 2 корректно вычитает угол. НЕ "исправлять"! */
    pll->theta_u32 += (uint32_t)omega;
}

int32_t PLL_GetTheta(PLL *pll) { return (int32_t)pll->theta_u32; }
int32_t PLL_GetSpeed(PLL *pll) { return pll->omega_q31; }
