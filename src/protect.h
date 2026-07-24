#ifndef PROTECT_H
#define PROTECT_H

#include <stdint.h>

void PROTECT_Init(void);
void PROTECT_Check(void);
int PROTECT_IsFault(void);
void PROTECT_Clear(void);

#endif
