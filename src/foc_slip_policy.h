#ifndef FOC_SLIP_POLICY_H
#define FOC_SLIP_POLICY_H

#include <stdbool.h>
#include <stdint.h>

/* These are FOC-cycle units: 2^32 angle codes per electrical turn, 5 kHz. */
#define FOC_SLIP_POLICY_MIN_TR_US      1000
#define FOC_SLIP_POLICY_MIN_ID_INTERNAL 10
#define FOC_SLIP_POLICY_2PI_INV        159155
#define FOC_SLIP_POLICY_PHASE_PER_HZ   858993
#define FOC_SLIP_POLICY_MAX_HZ         5
#define FOC_SLIP_POLICY_MAX_DT         ((int32_t)((int64_t)FOC_SLIP_POLICY_MAX_HZ * \
                                                   FOC_SLIP_POLICY_PHASE_PER_HZ))

bool FocSlipPolicy_IsMeasuredTr(int32_t tr_us);

/* Return electrical phase increment due to induction-motor slip. An unknown
 * Tr is a declared encoder-only mode: it returns zero instead of guessing a
 * nominal rotor time constant. */
int32_t FocSlipPolicy_ComputeDelta(int32_t tr_us,
                                   int32_t id_internal,
                                   int32_t iq_internal);

#endif
