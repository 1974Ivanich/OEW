#ifndef PWM_BOARD_PINS_H
#define PWM_BOARD_PINS_H

#include <stdbool.h>
#include <stdint.h>

/* OEW-HS-1 board pin map
 *
 * TIM1 PWM: PC0/PC1/PC2 (CH1/2/3 AF2), PA7/PB0/PB1 (CH1N/2N/3N AF6)
 * TIM8 PWM: PC6/PC7/PC8 (CH1/2/3 AF4), PC10/PC11/PC12 (CH1N/2N/3N AF4)
 * Break:    PB12 TIM1_BKIN AF6, PD2 TIM8_BKIN AF4, FAULT_N active low
 * Feedback: PB11 SAFETY_OK input from external manual-reset latch
 * Arm:      PB4/PB5 ARM_REQ_A/B outputs to interposer only, active high
 * Watchdog: PB13 MCU_HB output to external watchdog, initial low
 * Trigger:  PB6 scope trigger, active high
 * Other:    PA0/PA1/PA6/PC4 ADC; PA2/PA3 USART2 AF7; PA15 TIM2_CH1 AF1
 *
 * PB4/PB5 are not IPM gate-enable authority after OEW-HS-1. The external
 * latch, watchdog and tri-state PWM buffers own physical command permission.
 */

void PWM_BoardPins_Init(void);

/* ARM_REQ controls the external command-buffer qualification only. */
void PWM_ArmRequestsEnable(void);
void PWM_ArmRequestsDisable(void);
bool PWM_ArmRequestsAsserted(void);

/* Read-only hardware feedback. All inputs are externally fail-low biased. */
bool PWM_SafetyOkIsHigh(void);
bool PWM_BreakInputsAreHigh(void);

/* Dedicated watchdog edge source. Call from foreground health owner only. */
void PWM_BoardHeartbeatToggle(void);
void PWM_BoardHeartbeatLow(void);

/* Compatibility names map to ARM_REQ, never directly to IPM gates. */
void PWM_GatesEnable(void);
void PWM_GatesDisable(void);
bool PWM_GatesAreEnabled(void);

void PWM_TriggerHigh(void);
void PWM_TriggerLow(void);

#endif /* PWM_BOARD_PINS_H */
