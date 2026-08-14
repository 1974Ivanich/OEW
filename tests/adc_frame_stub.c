#include "adc.h"

bool ADC_FrameIsControlValid(const AdcFrame *frame)
{
    return frame != 0 && frame->status == ADC_FRAME_VALID && frame->sequence != 0u;
}
