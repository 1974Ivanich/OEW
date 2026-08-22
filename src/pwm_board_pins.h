#ifndef PWM_BOARD_PINS_H
#define PWM_BOARD_PINS_H

#include <stdbool.h>

/* SD-direct board pin map
 *
 * TIM1 PWM: PC0/PC1/PC2 (CH1/2/3 AF2), PA7/PB0/PB1 (CH1N/2N/3N AF6)
 * TIM8 PWM: PC6/PC7/PC8 (CH1/2/3 AF4), PC10/PC11/PC12 (CH1N/2N/3N AF4)
 * SD1:      PB12 TIM1_BKIN AF6 + GPIO IDR, STEVAL-1 SD open-drain, active low
 * SD2:      PD2  TIM8_BKIN AF4 + GPIO IDR, STEVAL-2 SD open-drain, active low
 * Trigger:  PB6 scope/vflog trigger, active high
 * Other:    PA0/PA1/PA6/PC4 ADC; PA2/PA3 USART2 AF7; PA15 TIM2_CH1 AF1
 *
 * The removed interposer control and feedback signals have no board-level
 * authority here. The MCU never drives either SD net: both are timer AF inputs
 * with the STEVAL R28 external pull-up. gpio_set_af leaves PUPDR=0.
 */

void PWM_BoardPins_Init(void);

/* Both STEVAL modules are physically healthy only if both self-clearing SD
 * open-drain nets read high. This is a read-only direct hardware input. */
bool PWM_SdLinesAreHigh(void);
bool PWM_EmStop1IsHigh(void);   /* линия EM_STOP1 (J2-1 платы №1) — SD-цепь модуля через R28 */
bool PWM_EmStop2IsHigh(void);   /* линия EM_STOP2 (J2-1 платы №2) */


/* PB6 remains the independent vflog/scope trigger. */
void PWM_TriggerHigh(void);
void PWM_TriggerLow(void);

#endif /* PWM_BOARD_PINS_H */
