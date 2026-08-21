#include "cli.h"

#include <stdio.h>
#include <string.h>

#define AT_VALID_RS    (1U << 0)
#define AT_VALID_LS    (1U << 1)
#define AT_VALID_RR    (1U << 2)
#define AT_VALID_LM    (1U << 3)
#define AT_VALID_TR    (1U << 4)
#define AT_VALID_KE    (1U << 6)
#define AT_VALID_J     (1U << 7)
#define AT_VALID_PAIRS (1U << 8)

static void send_text(const CLI_Ops *ops, const char *text)
{
    if (ops->send != 0) ops->send(text);
}

static int8_t run_at(const CLI_Ops *ops, CLI_AutotuneKind kind, uint8_t reset_abort)
{
    int8_t rc;
    if (reset_abort && ops->autotune_abort_set != 0) ops->autotune_abort_set(0u);
    if (ops->adc_irq_disable != 0) ops->adc_irq_disable();
    rc = ops->autotune_run != 0 ? ops->autotune_run(kind) : -1;
    if (ops->adc_irq_enable != 0) ops->adc_irq_enable();
    return rc;
}

int CLI_ProcessLine(const char *line, const CLI_Ops *ops, CLI_State *state)
{
    unsigned int u1 = 0u, u2 = 0u, u3 = 0u, u4 = 0u;
    int a1 = 0, a2 = 0, a3 = 0, a4 = 0, a5 = 0, a6 = 0, a7 = 0, a8 = 0;
    if (line == 0 || ops == 0 || state == 0) return -1;

    if (strcmp(line, "a") == 0) {
        CLI_AdcRaw raw;
        ops->adc_start(); ops->adc_raw(&raw);
        ops->send_telem("@ADC:I1=%u:I2=%u:Ires=%u:VBUS=%u\r\n> ", raw.i1, raw.i2, raw.ires, raw.vbus);
    } else if (sscanf(line, "a=%u", &u1) == 1) {
        if (u1 == 0u) { state->adc_stream_period_ms = 0u; ops->send_dbg("ADC stream stopped\r\n> "); }
        else if (u1 >= 50u && u1 <= 1000u) {
            state->adc_stream_period_ms = u1; state->adc_stream_last_ms = ops->tick_ms();
            ops->send_dbg_fmt("ADC stream started: %u ms\r\n> ", u1);
        } else ops->send_dbg("err: N must be 0 or 50..1000\r\n> ");
    } else if (strcmp(line, "a?") == 0) {
        CLI_AdcOffsets off; ops->adc_offsets(&off);
        ops->send_telem("@ADC:STATUS:offset_i1=%u:stream=%lu\r\n> ", off.offset_i1,
                        (unsigned long)state->adc_stream_period_ms);
    } else if (strcmp(line, "c") == 0) {
        if (ops->pwm_is_enabled()) send_text(ops, "err: PWM running — stop FOC/Vf first\r\n> ");
        else {
            CLI_AdcOffsets off;
            ops->adc_irq_disable(); ops->adc_calibrate_256(); ops->adc_irq_enable(); ops->adc_offsets(&off);
            ops->send_telem("@ADC:CAL:offset_i1=%u:offset_i2=%u:offset_ires=%u\r\n> ",
                            off.offset_i1, off.offset_i2, off.offset_ires);
        }
    } else if (strcmp(line, "p?") == 0) {
        CLI_PwmStatus p; ops->pwm_status(&p);
        ops->send_telem("@PWM:CR1=%lu:CCER=%lu:BDTR=%lu:CNT=%lu\r\n> ",
                        (unsigned long)p.cr1, (unsigned long)p.ccer, (unsigned long)p.bdtr, (unsigned long)p.cnt);
    } else if (sscanf(line, "p=%u,%u,%u,%u", &u1, &u2, &u3, &u4) >= 3) {
        if (ops->pwm_is_enabled()) send_text(ops, "err: PWM running — stop FOC/Vf first\r\n> ");
        else {
            ops->pwm_set_debug((uint16_t)u1, (uint16_t)u2, u3, (uint8_t)u4);
            ops->send_telem("@PWM:OK:arr=%u:duty=%u:dt=%u\r\n> ", u1, u2, u3);
        }
    } else if (line[0] == '1' && line[1] == '\0') {
        if (ops->fault_is_active()) ops->send_dbg("FAULT! send 'f' to clear\r\n> ");
        else {
            int rc;
            ops->vf_stop(); rc = ops->foc_start();
            if (rc == 0) ops->send_dbg("FOC started\r\n> ");
            else ops->send_telem("@FOC:START:FAIL:rc=%d (0=OK -1=clock/fault -2=map_unverified -3=calib -4=arm)\r\n> ", rc);
        }
    } else if (line[0] == '0' && line[1] == '\0') {
        ops->foc_stop(); ops->send_dbg("FOC stopped\r\n> ");
    } else if (ops->mapcap_command != 0 && ops->mapcap_command(line)) {
        /* Commissioning command formatted by the production map-capture adapter. */
    } else if (line[0] == 'm' && line[1] == '\0') {
        if (ops->print_help != 0) ops->print_help();
    } else if (line[0] == 's' && line[1] == '\0') {
        if (ops->swo_test != 0) ops->swo_test(ops->tick_ms());
        send_text(ops, "SWO test sent\r\n> ");
    } else if (line[0] == 'f' && line[1] == '\0') {
        if (ops->foc_is_running() || ops->vf_is_running()) {
            ops->send_dbg_fmt("@FAULT:CLEAR:STATUS=%d\r\n> ", 2);
            ops->send_dbg("err: stop FOC/Vf first\r\n> ");
        } else {
            int rc = ops->fault_request_clear();
            if (rc == 0) { ops->adc_calibrate(); ops->send_dbg("fault cleared\r\n> "); }
            else { ops->send_dbg_fmt("@FAULT:CLEAR:STATUS=%d\r\n> ", rc);
                   ops->send_dbg("fault NOT cleared: Vbus/current still out of range\r\n> "); }
        }
    } else if (line[0] == 's' && line[1] == '=') {
        long rpm_tmp = 0; char trail = '\0'; int parsed = sscanf(line + 2, "%ld%c", &rpm_tmp, &trail);
        if (parsed < 1) send_text(ops, "err: no digits\r\n> ");
        else if (parsed > 1 && trail != '\0') send_text(ops, "err: trailing chars\r\n> ");
        else if (rpm_tmp > 50000 || rpm_tmp < -50000) send_text(ops, "err: out of range\r\n> ");
        else { ops->foc_set_speed((int32_t)rpm_tmp); ops->send_dbg_fmt("speed=%ld rpm\r\n> ", (long)ops->foc_get_speed()); }
    } else if (strcmp(line, "dump") == 0 || strcmp(line, "dump8") == 0) {
        CLI_PwmDump p;
        if (line[4] == '8') ops->pwm_dump8(&p); else ops->pwm_dump(&p);
        ops->send_telem(line[4] == '8' ? "@PWM8:DUMP:PSC=%lu:ARR=%lu:BDTR=0x%08lX:CR1=0x%08lX:CR2=0x%08lX:CCER=0x%08lX\r\n> " :
                         "@PWM:DUMP:PSC=%lu:ARR=%lu:BDTR=0x%08lX:CR1=0x%08lX:CR2=0x%08lX:CCER=0x%08lX\r\n> ",
                         (unsigned long)p.psc, (unsigned long)p.arr, (unsigned long)p.bdtr,
                         (unsigned long)p.cr1, (unsigned long)p.cr2, (unsigned long)p.ccer);
    } else if (strcmp(line, "dumpa") == 0) {
        uint32_t d[12]; ops->adc_diag(d);
        ops->send_telem("@ADUMP:SQR1=0x%08lX:CFGR=0x%08lX:SMPR1=0x%08lX:JSQR=0x%08lX:DIFSEL=0x%08lX:CR=0x%08lX:ISR=0x%08lX:DR=0x%04lX:JDR1=0x%04lX:JDR2=0x%04lX:JDR3=0x%04lX:JDR4=0x%04lX\r\n> ",
                        (unsigned long)d[0],(unsigned long)d[1],(unsigned long)d[2],(unsigned long)d[3],
                        (unsigned long)d[4],(unsigned long)d[5],(unsigned long)d[6],(unsigned long)d[7],
                        (unsigned long)d[8],(unsigned long)d[9],(unsigned long)d[10],(unsigned long)d[11]);
    } else if (strcmp(line, "pdump") == 0) {
        uint32_t d[22]; ops->pwm_full_dump(d);
        ops->send_telem("@PWM:FULL:SYS=%lu:CFGR=0x%08lX:T1:PSC=%u:ARR=%u:CCR=%u,%u,%u:BDTR=0x%08lX:CCER=0x%08lX:CR1=0x%08lX:CNT=%lu:T8:PSC=%u:ARR=%u:CCR=%u,%u,%u:BDTR=0x%08lX:CCER=0x%08lX:CR1=0x%08lX:CNT=%lu\r\n> ",
                        (unsigned long)d[0],(unsigned long)d[1],(unsigned)d[2],(unsigned)d[3],(unsigned)d[4],(unsigned)d[5],(unsigned)d[6],(unsigned long)d[7],(unsigned long)d[8],(unsigned long)d[9],(unsigned long)d[10],(unsigned)d[11],(unsigned)d[12],(unsigned)d[13],(unsigned)d[14],(unsigned)d[15],(unsigned long)d[16],(unsigned long)d[17],(unsigned long)d[18],(unsigned long)d[19]);
    } else if (strcmp(line, "sysinfo") == 0) {
        uint32_t s[4], c[4]; ops->pwm_sysinfo(s); ops->adc_counts(c);
        ops->send_telem("@SYS:CLK=%lu:PSC=%lu:TCLK=%lu:PLLCFGR=0x%08lx:OVR=%lu:JEOS=%lu:TO=%lu:JQOVF=%lu\r\n> ",
                        (unsigned long)s[0],(unsigned long)s[1],(unsigned long)s[2],(unsigned long)s[3],
                        (unsigned long)c[0],(unsigned long)c[1],(unsigned long)c[2],(unsigned long)c[3]);
    } else if (sscanf(line, "pp=%u", &u1) == 1) {
        if (u1 < 1u || u1 > 24u) send_text(ops, "err: pole pairs must be 1..24\r\n> ");
        else if (ops->foc_is_running() || ops->vf_is_running()) send_text(ops, "err: stop FOC/Vf first\r\n> ");
        else if (ops->foc_set_pole_pairs((uint8_t)u1) == 0) {
            CLI_MotorParams p; ops->motor_get(&p); p.pairs = (int32_t)u1; ops->motor_set(&p);
            ops->send_telem("pole_pairs=%u\r\n> ", u1);
        } else send_text(ops, "err: pole pairs not applied\r\n> ");
    } else if (sscanf(line, "vdc=%u", &u1) == 1) {
        if (u1 < 10u || u1 > 400u) send_text(ops, "err: VDC must be 10..400 V\r\n> ");
        else if (ops->foc_set_vdc_mv((int32_t)u1 * 1000) == 0)
            ops->send_telem("@VDC:OK:%lu mV (VBUS measured=%ld mV)\r\n> ", (unsigned long)u1 * 1000, (long)ops->foc_vbus_mv());
        else send_text(ops, "err: VDC not set\r\n> ");
    } else if (sscanf(line, "fwbase=%u", &u1) == 1) {
        if (u1 < 100u || u1 > 5000u) send_text(ops, "err: FW base speed must be 100..5000 rpm\r\n> ");
        else if (ops->foc_set_base_speed((int32_t)u1) == 0) ops->send_telem("fw_base_speed=%u rpm\r\n> ", u1);
        else send_text(ops, "err: can't set base speed\r\n> ");
    } else if (sscanf(line, "dt=%u", &u1) == 1) {
        if (u1 > 12700u) send_text(ops, "err: max 12700 ns\r\n> ");
        else if (ops->pwm_set_deadtime(u1) != 0) send_text(ops, "err: PWM running — stop FOC/Vf first\r\n> ");
        else ops->send_telem("@PWM:DT=%u ns (DTG=%lu)\r\n> ", u1, (unsigned long)ops->pwm_deadtime_reg());
    } else if (strcmp(line, "curve") == 0) {
        ops->autotune_print_curve(); send_text(ops, "\r\n> ");
    } else if (strcmp(line, "params") == 0) {
        ops->autotune_print_params(); ops->send_telem("@AP:%d\r\n> ", ops->foc_params_applied());
    } else if (strcmp(line, "irot") == 0 || strcmp(line, "inertia") == 0) {
        int8_t rc = ops->autotune_run(line[0] == 'i' && line[1] == 'r' ? CLI_AT_IROT : CLI_AT_INERTIA);
        send_text(ops, rc == 0 ? (line[1] == 'r' ? "@IROT:OK\r\n> " : "@INERTIA:OK\r\n> ") :
                       (line[1] == 'r' ? "@IROT:FAIL\r\n> " : "@INERTIA:FAIL\r\n> "));
    } else if (strcmp(line, "ch") == 0 || strcmp(line, "chu") == 0 || strcmp(line, "chv") == 0 || strcmp(line, "chw") == 0) {
        CLI_AutotuneKind k = strcmp(line, "ch") == 0 ? CLI_AT_CH : (line[2] == 'u' ? CLI_AT_CHU : line[2] == 'v' ? CLI_AT_CHV : CLI_AT_CHW);
        int8_t rc = run_at(ops, k, 0u);
        if (k == CLI_AT_CH) send_text(ops, rc == 0 ? "@AT:CH:OK\r\n> " : "@AT:CH:FAIL\r\n> ");
        else if (rc == 0) ops->send_telem("@AT:CH%c:OK\r\n> ", line[2]);
        else send_text(ops, "@AT:CHP:FAIL\r\n> ");
    } else if (strcmp(line, "iv") == 0 || strcmp(line, "pairs") == 0 || strcmp(line, "oew") == 0 || strcmp(line, "rr") == 0 || strcmp(line, "noload") == 0 || strcmp(line, "scope") == 0 || strcmp(line, "lspos") == 0 || strcmp(line, "idle") == 0) {
        CLI_AutotuneKind k = CLI_AT_IV; int8_t rc;
        if (strcmp(line, "pairs") == 0) k = CLI_AT_PAIRS;
        else if (strcmp(line, "oew") == 0) k = CLI_AT_OEW;
        else if (strcmp(line, "rr") == 0) k = CLI_AT_RR;
        else if (strcmp(line, "noload") == 0) k = CLI_AT_NOLOAD;
        else if (strcmp(line, "scope") == 0) k = CLI_AT_SCOPE;
        else if (strcmp(line, "lspos") == 0) k = CLI_AT_LSPOS;
        else if (strcmp(line, "idle") == 0) k = CLI_AT_IDLE;
        rc = run_at(ops, k, (uint8_t)(k >= CLI_AT_OEW));
        if (k == CLI_AT_IV) send_text(ops, rc == 0 ? "@AT:IV:OK\r\n> " : "@AT:IV:FAIL\r\n> ");
        else if (k == CLI_AT_PAIRS) send_text(ops, rc == 0 ? "@AT:PAIRS:RESULT_OK\r\n> " : "@AT:PAIRS:RESULT_FAIL\r\n> ");
        else if (k == CLI_AT_OEW) send_text(ops, rc == 0 ? "@AT:OEW:RESULT_OK\r\n> " : rc == -5 ? "@AT:OEW:ABORTED\r\n> " : "@AT:OEW:RESULT_FAIL\r\n> ");
        else if (k == CLI_AT_RR) send_text(ops, rc == 0 ? "@AT:RR:RESULT_OK\r\n> " : rc == -6 ? "@AT:RR:ABORTED\r\n> " : "@AT:RR:RESULT_FAIL\r\n> ");
        else if (k == CLI_AT_NOLOAD) send_text(ops, rc == 0 ? "@AT:NOLOAD:RESULT_OK\r\n> " : rc == -6 ? "@AT:NOLOAD:ABORTED\r\n> " : "@AT:NOLOAD:RESULT_FAIL\r\n> ");
        else if (k == CLI_AT_SCOPE) send_text(ops, rc == 0 ? "@SCOPE:RESULT_OK\r\n> " : "@SCOPE:RESULT_FAIL\r\n> ");
        else if (k == CLI_AT_LSPOS) send_text(ops, rc == 0 ? "@AT:LSPOS:RESULT_OK\r\n> " : rc == -5 ? "@AT:LSPOS:ABORTED\r\n> " : "@AT:LSPOS:RESULT_FAIL\r\n> ");
        else send_text(ops, rc == 0 ? "@IDLE:OK\r\n> " : rc == -5 ? "@IDLE:ABORTED\r\n> " : "@IDLE:FAIL\r\n> ");
    } else if (strcmp(line, "abort") == 0) {
        if (ops->autotune_abort_set != 0) ops->autotune_abort_set(1u);
        send_text(ops, "abort requested\r\n> ");
    } else if (sscanf(line, "pi=%u", &u1) == 1) {
        ops->autotune_calc_pi((int32_t)u1); send_text(ops, "> ");
    } else if (sscanf(line, "mp=%d,%d,%d,%d,%d,%d,%d,%d", &a1,&a2,&a3,&a4,&a5,&a6,&a7,&a8) >= 2) {
        CLI_MotorParams p; int rc; int32_t kp, ki, lsig;
        ops->motor_get(&p); p.rs = a1; p.ls = a2;
        if (a3 > 0) p.rr = a3;
        if (a4 > 0) p.lm = a4;
        if (a5 > 0) p.tr = a5;
        if (a6 > 0) p.ke = a6;
        if (a7 > 0) p.pairs = a7;
        if (a8 > 0) p.inertia = a8;
        p.measured_mask |= AT_VALID_RS | AT_VALID_LS;
        if (a3 > 0) p.measured_mask |= AT_VALID_RR;
        if (a4 > 0) p.measured_mask |= AT_VALID_LM;
        if (a5 > 0) p.measured_mask |= AT_VALID_TR;
        if (a6 > 0) p.measured_mask |= AT_VALID_KE;
        if (a7 > 0) p.measured_mask |= AT_VALID_PAIRS;
        if (a8 > 0) p.measured_mask |= AT_VALID_J;
        ops->motor_set(&p); rc = ops->foc_set_params(a1, a2, ops->foc_vbus_mv());
        if (rc == 0) { if (a7 >= 1 && a7 <= 24) (void)ops->foc_set_pole_pairs((uint8_t)a7); ops->foc_get_params(0, 0, &kp, &ki); lsig = ops->foc_sigma_l(); ops->send_telem("@MP:OK:Rs=%d:Ls=%d:Rr=%d:Lm=%d:Tr=%d:Ke=%d:p=%d:J=%d:Kp=%d:Ki=%d:Lsig=%ld:AP=1\r\n> ", a1,a2,a3,a4,a5,a6,a7,a8,(long)kp,(long)ki,(long)lsig); }
        else ops->send_telem("@MP:ERROR:%d\r\n> ", rc);
    } else if (strcmp(line, "mpapply") == 0) {
        CLI_MotorParams p; int rc; int32_t kp, ki, lsig;
        ops->motor_get(&p); rc = ops->foc_set_params(p.rs, p.ls, ops->foc_vbus_mv());
        if (rc == 0) { if (p.pairs >= 1 && p.pairs <= 24) (void)ops->foc_set_pole_pairs((uint8_t)p.pairs); ops->foc_get_params(0,0,&kp,&ki); lsig=ops->foc_sigma_l(); ops->send_telem("@MPAPPLY:OK:Rs=%ld:Ls=%ld:Rr=%ld:Lm=%ld:Tr=%ld:p=%d:Kp=%ld:Ki=%ld:Lsig=%ld:AP=1\r\n> ",(long)p.rs,(long)p.ls,(long)p.rr,(long)p.lm,(long)p.tr,(int)p.pairs,(long)kp,(long)ki,(long)lsig); }
        else ops->send_telem("@MPAPPLY:ERROR:%d\r\n> ", rc);
    } else if (strcmp(line, "piapply") == 0) {
        int32_t kp, ki, bw; int rc = ops->autotune_last_pi(&kp,&ki,&bw);
        if (rc == 0) { rc=ops->foc_set_pi(kp,ki); if(rc==0) ops->send_telem("@PI:APPLIED:Kp=%ld:Ki=%ld:AP=1\r\n> ",(long)kp,(long)ki); else ops->send_telem("@PI:ERROR:%d\r\n> ",rc); }
        else send_text(ops,"@PI:ERROR:NOT_CALCULATED\r\n> ");
    } else if (strcmp(line, "stats") == 0) { ops->autotune_print_stats(); send_text(ops, "> ");
    } else if (sscanf(line, "i=%d,%d", &a1, &a2) == 2) { ops->foc_set_current(a1,a2); ops->send_telem("@I:OK:Id=%ld:Iq=%ld\r\n> ",(long)a1,(long)a2);
    } else if (sscanf(line, "vf=%d", &a1) == 1) {
        if (a1 == 0) { ops->vf_stop(); state->vflog_period_ms=0u; ops->trig_low(); send_text(ops,"V/f stopped\r\n> "); }
        else if (a1 < -5000 || a1 > 5000) send_text(ops,"err: rpm range -5000..+5000\r\n> ");
        else if (ops->fault_is_active()) send_text(ops,"FAULT! send 'f' to clear\r\n> ");
        else { int rc; uint32_t tick; ops->foc_stop(); ops->trig_high(); tick=ops->tick_ms(); rc=ops->vf_start(a1); if(rc != 0) { ops->send_telem("V/f blocked: rc=%d (sample context unverified)\r\n> ",rc); state->vflog_period_ms=0u; ops->trig_low(); return CLI_EXIT_LOOP; } if(state->vflog_period_ms==0u) state->vflog_period_ms=CLI_VFLOG_DEFAULT_PERIOD_MS; state->vflog_last_ms=ops->tick_ms(); ops->send_telem("V/f started: %d rpm\r\n@TRIG:tick=%lu\r\n> ",a1,(unsigned long)tick); }
    } else if (sscanf(line, "vflog=%u", &u1) == 1) {
        if(u1==0u) { state->vflog_period_ms=0u; send_text(ops,"vflog stopped\r\n> "); }
        else if(u1>=10u && u1<=1000u) { state->vflog_period_ms=u1; state->vflog_last_ms=ops->tick_ms(); ops->send_telem("vflog started: %u ms\r\n> ",u1); }
        else send_text(ops,"err: N must be 0 or 10..1000\r\n> ");
    } else if (strcmp(line,"vf?")==0) {
        CLI_VfStatus v; ops->vf_status(&v); ops->send_telem("@VF:target=%ld:meas=%ld:fe=%ld:fslip=%ld:vmag=%ld\r\n> ",(long)v.target,(long)v.measured,(long)v.fe,(long)v.slip,(long)v.vmag);
    } else if (strcmp(line,"enc")==0) {
        CLI_EncoderStatus e; ops->encoder_status(&e); ops->send_telem("@ENC:angle=%u:speed=%ld:period_us=%lu:pulse_us=%lu:err=%u\r\n> ",(unsigned)e.angle,(long)e.speed,(unsigned long)e.period,(unsigned long)e.pulse,(unsigned)e.error);
    } else if (sscanf(line,"vfk=%d,%d",&a1,&a2)==2) {
        CLI_VfStatus v; ops->vf_set_params(a1,a2); ops->vf_status(&v); ops->send_telem("V/f params: boost=%ld%% rated=%ldHz\r\n> ",(long)v.boost,(long)v.rated);
    } else { send_text(ops, "unknown\r\n> "); return 0; }
    return 1;
}
