#ifndef PWM_H
#define PWM_H

#include <stdint.h>

void PWM_Init(void);
void PWM_SetDuty1(uint16_t u, uint16_t v, uint16_t w);
void PWM_SetDuty2(uint16_t u, uint16_t v, uint16_t w);
void PWM_Enable(void);
void PWM_Disable(void);
void PWM_SetDeadTimeComp(int32_t dt_ticks);

#endif
