#ifndef CURRENT_MAP_SELECTOR_H
#define CURRENT_MAP_SELECTOR_H

#include <stdbool.h>
#include <stdint.h>
#include "pwm.h"   /* PwmSampleContext */

/* Fail-closed selector for the OEW two-shunt reconstruction map.
 * Both functions return false until ch/chu/chv/chw scope evidence has
 * produced a verified sector/window table. Returning false is safer than
 * emitting a placeholder context such as (0,0,true). */
bool CurrentMap_SelectInitialStartupContext(PwmSampleContext *context,
                                            int16_t *mu, int16_t *mv, int16_t *mw);
bool CurrentMap_SelectNextContext(int16_t mu, int16_t mv, int16_t mw,
                                  PwmSampleContext *context);

#endif /* CURRENT_MAP_SELECTOR_H */
