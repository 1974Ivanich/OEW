#ifndef MAP_CAPTURE_PORT_H
#define MAP_CAPTURE_PORT_H

#include <stdbool.h>

#include "map_capture.h"

/* Initialise the immutable project hooks after PWM/ADC/protection/FOC/Vf/
 * autotune initialisation and before ADC1_2_IRQn is enabled. This function
 * never enables a bridge or an injected conversion. */
bool MapCapturePort_Init(void);

/* Invoke from the TIM1 update ISR only while service capture is active. The
 * handler owns no UART and cannot start normal control. */
void MapCapturePort_OnPwmPeriod(void);

/* Invoke only after the central protection path has already latched its own
 * hardware/protection fault. It ends a capture session without double-latch. */
void MapCapturePort_OnProtectionLatched(void);

#endif /* MAP_CAPTURE_PORT_H */
