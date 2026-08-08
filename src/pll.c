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
    /* Clamp omega and integrator to physical limits */
    if(omega_q31 > PLL_OMEGA_MAX_Q31) omega_q31 = PLL_OMEGA_MAX_Q31;
    if(omega_q31 < -PLL_OMEGA_MAX_Q31) omega_q31 = -PLL_OMEGA_MAX_Q31;
    pll->omega_q31 = omega_q31;
    pll->integrator = CLAMP(omega_q31, -PLL_INTEGRATOR_MAX, PLL_INTEGRATOR_MAX);
}

void PLL_Update(PLL *pll, int32_t emf_alpha, int32_t emf_beta) {
    /* Нормализуем EMF. CORDIC принимает Q1.31, EMF у нас в Q15 → <<16.
     * int64 cast — корректное преобразование знаковых (UB-safe). */
    int32_t emf_a_q31 = (int32_t)((int64_t)emf_alpha << 16);
    int32_t emf_b_q31 = (int32_t)((int64_t)emf_beta  << 16);
    int32_t mod_q31, angle;
    CORDIC_Modulus(emf_a_q31, emf_b_q31, &mod_q31, &angle);
    int32_t mod_q15 = mod_q31 >> 16;
    if(mod_q15 < PLL_EMF_MIN_Q15) return;  /* шум на малой скорости — не обновляем */

    /* Нормализация: E/|E| в Q15. mod_q31 — Q1.31, emf — Q15. */
    int32_t e_norm_a = (int32_t)(((int64_t)emf_alpha << 15) / mod_q15);
    int32_t e_norm_b = (int32_t)(((int64_t)emf_beta  << 15) / mod_q15);

    /* Ошибка PLL: err = -Eα*sin(θ) + Eβ*cos(θ)
     * Q15×Q15 = Q30; сумма двух Q30 может превысить int32 — считаем в int64 */
    int32_t s, c;
    CORDIC_SinCos((int32_t)pll->theta_u32, &s, &c);
    int32_t err = (int32_t)((-(int64_t)e_norm_a * s + (int64_t)e_norm_b * c) >> 15);

    /* PI. Единицы omega: приращение угла q31 за один FOC-цикл (Ts).
     * Прямое интегрирование theta += omega без умножения на ts_us —
     * исключает переполнение int32 на высоких скоростях. */
    /* Anti-windup: ограничение интегратора. */
    pll->integrator += (int32_t)(((int64_t)pll->ki * err) >> 15);
    pll->integrator = CLAMP(pll->integrator, -PLL_INTEGRATOR_MAX, PLL_INTEGRATOR_MAX);
    int32_t omega = (int32_t)(((int64_t)pll->kp * err) >> 15) + pll->integrator;
    /* Насыщение omega — предотвращает выход за физически допустимый диапазон
     * при PLL glitch / кратковременном шуме EMF. */
    omega = CLAMP(omega, -PLL_OMEGA_MAX_Q31, PLL_OMEGA_MAX_Q31);
    pll->omega_q31 = omega;

    /* Интегрирование угла. Переполнение uint32_t определено стандартом C
     * как wrap-around — корректный модуль 2π в формате q31.
     * Приведение (uint32_t)omega НАМЕРЕННОЕ: отрицательная omega (реверс)
     * через дополнение до 2 корректно вычитает угол. НЕ "исправлять"! */
    pll->theta_u32 += (uint32_t)omega;
}

int32_t PLL_GetTheta(PLL *pll) { return (int32_t)pll->theta_u32; }

/* Для АД: PLL отслеживает угол EMF (φ_EMF), а FOC нужен угол потока ротора (θ_ψr).
 * e = dψr/dt = ω·|ψr|·[-sin(θ_ψr), cos(θ_ψr)]
 * → φ_EMF = θ_ψr + sign(ω)·π/2
 * → θ_ψr = φ_EMF − sign(ω)·π/2
 * В Q0.32: π/2 = 0x40000000. uint32_t wrap-around корректен. */
int32_t PLL_GetFluxTheta(PLL *pll) {
    if(pll->omega_q31 >= 0) return (int32_t)(pll->theta_u32 - 0x40000000U);
    else                    return (int32_t)(pll->theta_u32 + 0x40000000U);
}

int32_t PLL_GetSpeed(PLL *pll) { return pll->omega_q31; }
