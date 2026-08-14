#include "vf_start.h"

/* VF_Update вызывается из FOC_Run (ADC ISR, 5 кГц) → Ts = 200 мкс.
 * Ревью observer/VFStart: константы вынесены, чтобы связь с частотой
 * вызова была ЯВНОЙ — при смене 5 кГц → 10 кГц обе обязаны поменяться. */
#define VF_TS_US        200U
#define VF_TICKS_PER_MS (1000U / VF_TS_US)   /* 5 тиков @5 кГц = 1 мс */
/* Ревью VFS-03: точный Δθ за цикл — 2^32/(60·5000) = 14316.5577, НЕ 14317.
 * Константа 14317 даёт дрейф +0.4423 ед. фазы на eRPM за цикл: за ramp
 * 0→120000 eRPM/2с набегает ~22°, при hold после рампы — ещё больше.
 * Точная формула (int64, округление): delta = (erpm·2^32 + 150000)/300000. */
#define VF_DELTA_NUM 4294967296LL   /* 2^32 — полный эл. оборот */
#define VF_DELTA_DEN 300000LL       /* 60 × 5000 (Ts = 200 мкс) */

static inline int64_t vf_theta_delta(int32_t erpm) {
    return ((int64_t)erpm * VF_DELTA_NUM + VF_DELTA_DEN / 2) / VF_DELTA_DEN;
}

void VF_Init(VFStart *vf, int32_t target_erpm, int32_t ramp_ms) {
    vf->target_speed = target_erpm;
    vf->current_speed = 0;
    vf->ramp_time_ms = (ramp_ms > 0) ? ramp_ms : 1;  /* защита от div-by-zero */
    vf->theta_u32 = 0;
    vf->tick_counter = 0;
    vf->complete = 0;
}

void VF_Update(VFStart *vf) {
    if(vf->complete) {
        /* Ревью VFS-01: завершение рампы НЕ останавливает генератор угла.
         * Раньше после complete=1 угол замирал, и при неудачном handoff
         * (условия в foc.c не выполнены) FOC оставался в STARTUP с
         * НЕПОДВИЖНЫМ полем и DC-током в обмотках неопределённо долго.
         * Угол продолжает вращаться на целевой скорости до VF_SetTarget
         * (complete=0, новая рампа) или VF_Init. */
        vf->theta_u32 += (uint32_t)vf_theta_delta(vf->target_speed);
        return;
    }

    vf->tick_counter++;
    uint32_t elapsed_ms = vf->tick_counter / VF_TICKS_PER_MS; // 200us * 5 = 1ms

    if(elapsed_ms < (uint32_t)vf->ramp_time_ms) {
        vf->current_speed = vf->target_speed * (int32_t)elapsed_ms / vf->ramp_time_ms;
    } else {
        vf->current_speed = vf->target_speed;
        vf->complete = 1;
    }

    /* Интегрирование угла. current_speed — ЭЛЕКТРИЧЕСКИЕ об/мин (rpm × pole pairs).
     * Δθ(q31) за шаг Ts=200 мкс: (e_rpm/60) об/с × 2^32 × 200e-6.
     * Приведение к uint32_t НАМЕРЕННОЕ: при отрицательной скорости (реверс)
     * арифметика дополнения до 2 + wrap-around uint32 дают корректное
     * вычитание угла по модулю 2π. НЕ "исправлять" на int32! */
    vf->theta_u32 += (uint32_t)vf_theta_delta(vf->current_speed);
}

/* Обновление целевой скорости на лету (электрические об/мин).
 * Рампа пересчитывается от текущей скорости без скачка угла:
 * tick_counter сбрасывается в точку, соответствующую текущей скорости
 * на новой рампе. theta_u32 не трогаем — угол непрерывен.
 *
 * При смене знака (current > 0, target < 0 или наоборот) формула
 * elapsed = current * ramp / target даёт отрицательное значение →
 * clamped to 0 → current_speed скачком падает до 0, затем рампа 0→target.
 * Это сознательное упрощение: реверс через нуль, а не через торможение.
 * Для плавного реверса нужно сначала тормозить до 0, затем менять target. */
void VF_SetTarget(VFStart *vf, int32_t target_erpm) {
    if(target_erpm == vf->target_speed) return;
    if(target_erpm != 0) {
        /* новая позиция на рампе: elapsed = current/target * ramp_time.
         * Корректно только при одинаковом знаке current и target. */
        int32_t elapsed_ms = (int32_t)(((int64_t)vf->current_speed * vf->ramp_time_ms) / target_erpm);
        if(elapsed_ms < 0) elapsed_ms = 0;
        if(elapsed_ms > vf->ramp_time_ms) elapsed_ms = vf->ramp_time_ms;
        /* Ревью VFS-06: без зашитой пятёрки — из VF_TICKS_PER_MS. */
        vf->tick_counter = (uint32_t)elapsed_ms * VF_TICKS_PER_MS;
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
