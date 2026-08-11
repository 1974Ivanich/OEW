#ifndef PWM_H
#define PWM_H

#include <stdint.h>

void PWM_SetDuty1(uint16_t u, uint16_t v, uint16_t w);
void PWM_SetDuty2(uint16_t u, uint16_t v, uint16_t w);
void PWM_Enable(void);
void PWM_Disable(void);

#endif /* PWM_H */
