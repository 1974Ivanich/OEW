#ifndef ADC_H
#define ADC_H

#include <stdbool.h>
#include <stdint.h>

bool ADC_InjectedIsArmed(void);
void ADC_InjectedStop(void);
void ADC_SetExpectedWindow(uint8_t sector, uint8_t window, bool valid);
void ADC_SetControlAdmission(bool enabled);

#endif /* ADC_H */
