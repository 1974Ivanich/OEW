#ifndef VF_START_H
#define VF_START_H

#include <stdint.h>

/* Open-loop I-f стартер (feedforward по току, не классический V/f).
 * Угол генерируется линейной рампой скорости; задания тока (момент/
 * намагничивание) задаются в foc.c через FOC_STARTUP_IQ/ID. */
typedef struct {
    int32_t target_speed;   /* целевая скорость, электрические об/мин */
    int32_t current_speed;  /* текущая скорость на рампе */
    int32_t ramp_time_ms;
    uint32_t theta_u32;     /* угол q31; uint32 wrap-around = модуль 2π */
    uint32_t tick_counter;
    int complete;
} VFStart;

void VF_Init(VFStart *vf, int32_t target_erpm, int32_t ramp_ms);
void VF_SetTarget(VFStart *vf, int32_t target_erpm);  /* обновление цели на лету */
void VF_Update(VFStart *vf);
int VF_IsComplete(VFStart *vf);
int32_t VF_GetTheta(VFStart *vf);  /* возвращает (int32_t)theta_u32 */
int32_t VF_GetSpeed(VFStart *vf);

#endif
