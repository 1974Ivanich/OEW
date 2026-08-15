#ifndef PROTECT_H
#define PROTECT_H

#include <stdint.h>

typedef enum {
    PROTECT_FAULT_NONE = 0,
    PROTECT_FAULT_HARDWARE_BREAK = 18
} ProtectFaultReason;

typedef enum {
    PROTECT_CLEAR_OK = 0,
    PROTECT_CLEAR_NOT_LATCHED,
    PROTECT_CLEAR_CONTROL_ACTIVE,
    PROTECT_CLEAR_SAMPLE_INVALID,
    PROTECT_CLEAR_VALUES_UNSAFE
} ProtectClearStatus;

void PROTECT_Init(void);
int PROTECT_IsFault(void);
void PROTECT_LatchFault(ProtectFaultReason reason);
ProtectClearStatus PROTECT_RequestClear(void);

#endif /* PROTECT_H */
