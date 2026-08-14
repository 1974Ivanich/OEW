#ifndef OBSERVER_H
#define OBSERVER_H

#include <stdint.h>

typedef struct {
    int32_t R_mOhm;
    int32_t L_uH;
    int32_t Ts_us;
    int32_t Vdc_mV;     /* базис напряжения: 32767 (Q15) = Vdc */
    int32_t emf_alpha, emf_beta;  /* Q15, 32768 = Vdc */
    int32_t prev_ia_ma, prev_ib_ma;  /* токи в мА для высокоразрешающей производной */
    uint8_t prev_valid; /* 0 после Init — первый вызов пропускает dI/dt */
    /* Ревью OBS-01/03/06: контракт качества оценки */
    int32_t dia_f_ma, dib_f_ma;  /* dirty derivative: фильтрованная dI/dt (мА/цикл) */
    uint8_t current_glitch;      /* выброс dI/dt (sentinel/glitch) — EMF цикла невалидна */
    uint8_t emf_saturated;       /* компонента EMF достигла Q15-предела (clamp сработал) */
    uint8_t signal_valid;        /* 1 = оценка пригодна (нет glitch/saturation/Vbus<1В) */
} BEMFObserver;

void BEMF_Init(BEMFObserver *obs, int32_t r_mohm, int32_t l_uh, int32_t ts_us, int32_t vdc_mv);
/* Контракт времени (OBS-05): BEMF_Update(V[k−1], I[k]) — напряжение
 * ПРЕДЫДУЩЕГО управляющего цикла (реконструированное из реальной модуляции),
 * токи — текущей injected-выборки ADC (после применения V[k−1]). */
/* ia_ma, ib_ma — токи в мА (не mA/100). Vα/Vβ — Q15 (32768 = Vdc). */
void BEMF_Update(BEMFObserver *obs, int32_t valpha, int32_t vbeta, int32_t ia_ma, int32_t ib_ma);
int32_t BEMF_GetMagnitude(BEMFObserver *obs);  /* |EMF| — для проверки качества оценки */
uint8_t BEMF_IsValid(const BEMFObserver *obs); /* 1 = оценка пригодна (OBS-06) */

#endif
