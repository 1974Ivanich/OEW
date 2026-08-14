#ifndef HS1_DIAG_H
#define HS1_DIAG_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* A read-only snapshot for T0–T9 evidence. No member is an authorization
 * token; `interlock` is sampled only for reporting and normal PWM gates remain
 * owned by pwm.c. */
typedef struct {
    uint32_t break_tim1_count;
    uint32_t break_tim8_count;
    uint32_t last_break_source;  /* 0 none, 1 TIM1, 8 TIM8 */
    uint32_t tim1_sr;
    uint32_t tim8_sr;
    uint32_t tim1_bdtr;
    uint32_t tim8_bdtr;
    uint32_t tim1_af1;
    uint32_t tim8_af1;
    uint32_t tim1_arr;
    uint32_t tim8_arr;
    uint32_t tim1_ccr[3];
    uint32_t tim8_ccr[3];
    int32_t fault_reason;
    uint8_t interlock;
    uint8_t safety_ok_pb11;
    uint8_t bkin_pb12_high;
    uint8_t bkin_pd2_high;
    uint8_t arm_req_a_pb4;
    uint8_t arm_req_b_pb5;
    uint8_t heartbeat_pb13;
} Hs1DiagSnapshot;

void HS1Diag_Init(void);

/* ISR-safe, bounded counter notifications. These functions never format or
 * send UART output, never clear flags, never call PWM_Enable/Disable, and
 * never change safety/interlock state. Call only after the handler observes
 * its own BIF/B2IF source. */
void HS1Diag_OnTim1BreakIrq(void);
void HS1Diag_OnTim8BreakIrq(void);

/* Reads one internally consistent diagnostic snapshot. A false result means
 * an ISR kept changing the break-counter seqlock; emit no incomplete line and
 * retry from foreground. */
bool HS1Diag_Read(Hs1DiagSnapshot *out);

/* Formats exactly one UART-safe foreground line. The caller owns transport;
 * never call this function from a break ISR. Return value is snprintf-style. */
int HS1Diag_FormatLine(char *dst, size_t dst_size, const Hs1DiagSnapshot *snapshot);

#endif /* HS1_DIAG_H */
