#ifndef PWM_BOARD_PINS_H
#define PWM_BOARD_PINS_H

#include <stdbool.h>
#include <stdint.h>

/*
 * OEW power-stage board pins
 *
 * TIM1: PC0/PC1/PC2 (CH1/2/3 AF2), PA7/PB0/PB1 (CH1N/2N/3N AF6)
 * TIM8: PC6/PC7/PC8 (CH1/2/3 AF4), PC10/PC11/PC12 (CH1N/2N/3N AF4)
 * Gates: PB4=EN1, PB5=EN2, both active high
 * Scope trigger: PB6=TRIG, active high
 * Other board mux: PA0/PA1/PA6/PC4 ADC analog, PA2/PA3 USART2 AF7,
 *                 PA15 TIM2_CH1 encoder AF1
 */

/* Configure all board GPIO clocks/pinmux; gates and trigger remain low. */
void PWM_BoardPins_Init(void);

/* Active-high gate driver enables. Call Enable only after CCER/MOE/CEN are ready. */
void PWM_GatesEnable(void);
void PWM_GatesDisable(void);
bool PWM_GatesAreEnabled(void);

/* Optional logic-analyser synchronisation pin. */
void PWM_TriggerHigh(void);
void PWM_TriggerLow(void);

#endif /* PWM_BOARD_PINS_H */
