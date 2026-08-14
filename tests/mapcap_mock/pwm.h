#ifndef PWM_H
#define PWM_H

#include <stdbool.h>
#include <stdint.h>

/* Mock синхронизирован с OEW-HS-1 PWM replacement API. */
#define PWM_ENABLE_OK                       0
#define PWM_ENABLE_FAULT_LATCHED           -1
#define PWM_ENABLE_CLOCK_FAILED            -2
#define PWM_ENABLE_CONTEXT_INVALID         -3
#define PWM_ENABLE_INTERLOCK_OPEN          -4
#define PWM_ENABLE_ADC_NOT_ARMED           -5
#define PWM_ENABLE_SERVICE_PROFILE_REQUIRED -6
#define PWM_ENABLE_SERVICE_PATTERN_INVALID -7

typedef struct {
    uint8_t sector;
    uint8_t window;
    bool valid;
} PwmSampleContext;

typedef struct {
    uint16_t tim1_ccr[3];
    uint16_t tim8_ccr[3];
    uint8_t sector_candidate;
    uint8_t window_candidate;
    uint32_t trigger_revision;
} PwmServiceCapturePattern;

bool PWM_HardwareInterlockHealthy(void);
bool PWM_BreakFaultActive(void);
int  PWM_ServiceCaptureStart(const PwmServiceCapturePattern *pattern);
void PWM_Disable(void);
void PWM_HeartbeatToggle(void);
bool PWM_SafetyOkIsHigh(void);
bool PWM_BreakInputsAreHigh(void);

#endif
