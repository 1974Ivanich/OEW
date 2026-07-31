#include "vf_start.h"

void VF_Init(VFStart *vf, int32_t target_erpm, int32_t ramp_ms) {
    vf->target_speed = target_erpm;
    vf->current_speed = 0;
    vf->ramp_time_ms = ramp_ms;
    vf->theta_u32 = 0;
    vf->tick_counter = 0;
    vf->complete = 0;
}

void VF_Update(VFStart *vf) {
    if(vf->complete) return;

    vf->tick_counter++;
    uint32_t elapsed_ms = vf->tick_counter / 5; // 200us * 5 = 1ms

    if(elapsed_ms < (uint32_t)vf->ramp_time_ms) {
        vf->current_speed = vf->target_speed * (int32_t)elapsed_ms / vf->ramp_time_ms;
    } else {
        vf->current_speed = vf->target_speed;
        vf->complete = 1;
    }

    /* Интегрирование угла. current_speed — ЭЛЕКТРИЧЕСКИЕ об/мин (rpm × pole pairs).
     * Δθ(q31) за шаг Ts=200 мкс: (e_rpm/60) об/с × 2^32 × 200e-6 ≈ e_rpm × 14317.
     * Приведение к uint32_t НАМЕРЕННОЕ: при отрицательной скорости (реверс)
     * арифметика дополнения до 2 + wrap-around uint32 дают корректное
     * вычитание угла по модулю 2π. НЕ "исправлять" на int32! */
    int64_t delta = (int64_t)vf->current_speed * 14317;
    vf->theta_u32 += (uint32_t)delta;
}

/* Обновление целевой скорости на лету (электрические об/мин).
 * Рампа пересчитывается от текущей скорости без скачка угла:
 * tick_counter сбрасывается в точку, соответствующую текущей скорости
 * на новой рампе. theta_u32 не трогаем — угол непрерывен. */
void VF_SetTarget(VFStart *vf, int32_t target_erpm) {
    if(target_erpm == vf->target_speed) return;
    if(target_erpm != 0) {
        /* новая позиция на рампе: elapsed = current/target * ramp_time */
        int32_t elapsed_ms = (int32_t)(((int64_t)vf->current_speed * vf->ramp_time_ms) / target_erpm);
        if(elapsed_ms < 0) elapsed_ms = 0;
        if(elapsed_ms > vf->ramp_time_ms) elapsed_ms = vf->ramp_time_ms;
        vf->tick_counter = (uint32_t)elapsed_ms * 5;   /* 1 мс = 5 тиков по 200 мкс */
    } else {
        vf->tick_counter = 0;
        vf->current_speed = 0;
    }
    vf->target_speed = target_erpm;
    vf->complete = 0;
}

int VF_IsComplete(VFStart *vf) { return vf->complete; }
int32_t VF_GetTheta(VFStart *vf) { return (int32_t)vf->theta_u32; }
int32_t VF_GetSpeed(VFStart *vf) { return vf->current_speed; }
