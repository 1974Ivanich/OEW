#ifndef PROTECT_H
#define PROTECT_H

#include <stdint.h>

typedef enum {
    PROTECT_FAULT_NONE = 0,
    PROTECT_FAULT_OVERCURRENT,
    PROTECT_FAULT_VBUS_HIGH,
    PROTECT_FAULT_VBUS_LOW
} ProtectFaultReason;

void PROTECT_Init(void);
void PROTECT_Check(void);
int PROTECT_IsFault(void);
int PROTECT_GetFaultReason(void);
void PROTECT_Clear(void);

#endif
