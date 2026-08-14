#ifndef PWM_H
#define PWM_H

#include <stdbool.h>
#include "map_capture.h"

#define PWM_ENABLE_OK 0

bool PWM_HardwareInterlockHealthy(void);
bool PWM_ServiceCaptureValidate(const MapCaptureRequest *request);
int PWM_ServiceCaptureStart(const MapCaptureRequest *request);
void PWM_ServiceCaptureStop(void);
bool PWM_ServiceCaptureSnapshot(MapCapturePwmSnapshot *out);

#endif
