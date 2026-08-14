#include "current_map_selector.h"

/*
 * Deliberate integration gate.
 *
 * The physical OEW sector/window classifier does not exist until ch/chu/chv/
 * chw scope evidence has produced a verified map. Returning false is safer
 * than emitting a placeholder context such as (0,0,true).
 */
bool CurrentMap_SelectInitialStartupContext(PwmSampleContext *context,
                                            int16_t *mu, int16_t *mv, int16_t *mw)
{
    (void)context;
    (void)mu;
    (void)mv;
    (void)mw;
    return false;
}

bool CurrentMap_SelectNextContext(int16_t mu, int16_t mv, int16_t mw,
                                  PwmSampleContext *context)
{
    (void)mu;
    (void)mv;
    (void)mw;
    (void)context;
    return false;
}
