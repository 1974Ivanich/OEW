#include "cli.h"
#include <stdio.h>
#include <string.h>

static void send_text(const CLI_Ops *ops, const char *text)
{
    if (ops != 0 && ops->send != 0) ops->send(text);
}

int CLI_ProcessLine(const char *line, const CLI_Ops *ops, CLI_State *state)
{
    char out[160];
    unsigned int u1, u2, u4;
    int32_t a1, a2, a3;
    if (line == 0 || ops == 0 || state == 0) return -1;

    if (strcmp(line, "p?") == 0 && ops->pwm_status != 0) {
        uint32_t cr1, ccer, bdtr, cnt;
        ops->pwm_status(&cr1, &ccer, &bdtr, &cnt);
        (void)snprintf(out, sizeof(out), "@PWM:CR1=%lu:CCER=%lu:BDTR=%lu:CNT=%lu\r\n> ",
                       (unsigned long)cr1, (unsigned long)ccer,
                       (unsigned long)bdtr, (unsigned long)cnt);
        send_text(ops, out);
        return 1;
    }
    if (sscanf(line, "p=%u,%u,%d,%u", &u1, &u2, &a1, &u4) == 4 && ops->pwm_set != 0) {
        int rc = ops->pwm_set((uint16_t)u1, (uint16_t)u2, (uint32_t)a1, (uint8_t)u4);
        if (rc != 0) send_text(ops, "err: PWM running — stop FOC/Vf first\r\n> ");
        else {
            (void)snprintf(out, sizeof(out), "@PWM:OK:arr=%u:duty=%u:dt=%d\r\n> ", u1, u2, a1);
            send_text(ops, out);
        }
        return 1;
    }
    if (strcmp(line, "1") == 0 && ops->foc_start != 0) {
        if (ops->fault_is_active != 0 && ops->fault_is_active()) {
            send_text(ops, "FAULT! send 'f' to clear\r\n> ");
        } else {
            int rc = ops->foc_start();
            if (rc == 0) send_text(ops, "FOC started\r\n> ");
            else {
                (void)snprintf(out, sizeof(out), "@FOC:START:FAIL:rc=%d (0=OK -1=clock/fault -2=map_unverified -3=calib -4=arm)\r\n> ", rc);
                send_text(ops, out);
            }
        }
        return 1;
    }
    if (strcmp(line, "0") == 0 && ops->foc_stop != 0) {
        ops->foc_stop();
        send_text(ops, "FOC stopped\r\n> ");
        return 1;
    }
    if (strcmp(line, "f") == 0 && ops->fault_clear != 0) {
        ops->fault_clear();
        send_text(ops, "fault cleared\r\n> ");
        return 1;
    }
    if (sscanf(line, "mp=%d,%d,%d", &a1, &a2, &a3) == 3 && ops->foc_set_params != 0) {
        int rc = ops->foc_set_params(a1, a2, a3);
        if (rc == 0) {
            state->motor_rs = a1; state->motor_ls = a2; state->motor_vbus = a3;
            state->params_valid = 1;
            send_text(ops, "@MP:OK\r\n> ");
        } else send_text(ops, "@MP:ERROR\r\n> ");
        return 1;
    }
    if (strcmp(line, "mpapply") == 0 && ops->foc_apply_params != 0) {
        int rc = ops->foc_apply_params();
        send_text(ops, rc == 0 ? "@MP:OK\r\n> " : "@MP:ERROR\r\n> ");
        return 1;
    }
    if (sscanf(line, "vf=%d", &a1) == 1 && ops->vf_start != 0) {
        if (a1 == 0) {
            if (ops->vf_stop != 0) ops->vf_stop();
            send_text(ops, "V/f stopped\r\n> ");
        } else if (ops->fault_is_active != 0 && ops->fault_is_active()) {
            send_text(ops, "FAULT! send 'f' to clear\r\n> ");
        } else {
            int rc = ops->vf_start(a1);
            if (rc == 0) {
                (void)snprintf(out, sizeof(out), "V/f started: %d rpm\r\n> ", (int)a1);
                send_text(ops, out);
            } else {
                (void)snprintf(out, sizeof(out), "V/f blocked: rc=%d (sample context unverified)\r\n> ", rc);
                send_text(ops, out);
            }
        }
        return 1;
    }
    if (strcmp(line, "enc") == 0 && ops->encoder_status != 0) {
        uint16_t angle; int32_t speed; uint32_t period, pulse; uint8_t error;
        (void)ops->encoder_status(&angle, &speed, &period, &pulse, &error);
        (void)snprintf(out, sizeof(out), "@ENC:angle=%u:speed=%ld:period_us=%lu:pulse_us=%lu:err=%u\r\n> ",
                       (unsigned)angle, (long)speed, (unsigned long)period,
                       (unsigned long)pulse, (unsigned)error);
        send_text(ops, out);
        return 1;
    }
    send_text(ops, "unknown\r\n> ");
    return 0;
}
