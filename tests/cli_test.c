#include "cli.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>

static char uart_out[4096];
static char dbg_out[4096];
static int failures;
static int checks;
static int irq_disable_count;
static int irq_enable_count;
static int adc_cal_count;
static int at_rc;
static int at_last_rc;
static int vf_rc;
static int foc_start_rc;
static int fault_active;
static int foc_running_flag;
static int vf_running_flag;
static int fault_clear_rc;
static int foc_set_params_rc;
static int foc_set_pi_rc;
static int foc_pp_rc;
static int foc_vdc_rc;
static int foc_base_rc;
static int pwm_dt_rc;
static uint32_t pwm_enabled_flag;
static uint32_t tick_now = 123u;
static uint32_t swo_count;
static uint32_t help_count;
static uint32_t mapcap_enabled;
static int32_t last_speed;
static int32_t last_id;
static int32_t last_iq;
static int32_t last_pp;
static int32_t last_vdc;
static int32_t last_base;
static int32_t last_vfk_boost;
static int32_t last_vfk_rated;
static CLI_AutotuneKind last_at_kind;
static uint8_t last_abort;
static CLI_MotorParams motor;

static void append(char *out, const char *fmt, va_list ap)
{
    size_t n = strlen(out);
    (void)vsnprintf(out + n, sizeof(uart_out) - n, fmt, ap);
}

static void send_text(const char *s)
{
    strncat(uart_out, s, sizeof(uart_out) - strlen(uart_out) - 1u);
}

static void send_telem(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    append(uart_out, fmt, ap);
    va_end(ap);
}

static void send_dbg(const char *s)
{
    strncat(dbg_out, s, sizeof(dbg_out) - strlen(dbg_out) - 1u);
}

static void send_dbg_fmt(const char *fmt, ...)
{
    va_list ap;
    size_t n = strlen(dbg_out);
    va_start(ap, fmt);
    (void)vsnprintf(dbg_out + n, sizeof(dbg_out) - n, fmt, ap);
    va_end(ap);
}

static void reset_output(void)
{
    uart_out[0] = '\0';
    dbg_out[0] = '\0';
}

static void reset_controls(void)
{
    irq_disable_count = 0;
    irq_enable_count = 0;
    adc_cal_count = 0;
    at_rc = 0;
    at_last_rc = 0;
    vf_rc = 0;
    foc_start_rc = -2;
    fault_active = 0;
    foc_running_flag = 0;
    vf_running_flag = 0;
    fault_clear_rc = 0;
    foc_set_params_rc = 0;
    foc_set_pi_rc = 0;
    foc_pp_rc = 0;
    foc_vdc_rc = 0;
    foc_base_rc = 0;
    pwm_dt_rc = 0;
    pwm_enabled_flag = 0u;
    last_speed = 123;
    last_id = 0;
    last_iq = 0;
    last_pp = 0;
    last_vdc = 0;
    last_base = 0;
    last_vfk_boost = 0;
    last_vfk_rated = 0;
    last_at_kind = CLI_AT_IROT;
    last_abort = 0u;
    tick_now = 123u;
    swo_count = 0u;
    help_count = 0u;
    mapcap_enabled = 0u;
    motor = (CLI_MotorParams){10, 500, 20, 700, 30, 40, 4, 5, 0u};
}

static void check(const char *name, int ok)
{
    ++checks;
    if (ok) printf("ok:   %s\n", name);
    else { ++failures; printf("FAIL: %s\n", name); }
}

static void expect_uart(const char *name, int rc, int expected_rc, const char *expected)
{
    check(name, rc == expected_rc && strcmp(uart_out, expected) == 0 && dbg_out[0] == '\0');
}

static void expect_dbg(const char *name, int rc, int expected_rc, const char *expected)
{
    check(name, rc == expected_rc && strcmp(dbg_out, expected) == 0 && uart_out[0] == '\0');
}

static uint32_t tick_ms(void) { return tick_now++; }
static int adc_start(void) { return 0; }
static void adc_raw(CLI_AdcRaw *v) { *v = (CLI_AdcRaw){1u, 2u, 3u, 4u}; }
static void adc_offsets(CLI_AdcOffsets *v) { *v = (CLI_AdcOffsets){11u, 12u, 13u}; }
static int adc_cal(void) { ++adc_cal_count; return 0; }
static void irq_off(void) { ++irq_disable_count; }
static void irq_on(void) { ++irq_enable_count; }
static void adc_diag(uint32_t out[14]) { unsigned i; for (i = 0u; i < 14u; ++i) out[i] = i; }

static void adc_counts(uint32_t out[4]) { out[0] = 1u; out[1] = 2u; out[2] = 3u; out[3] = 4u; }
static uint32_t pwm_enabled(void) { return pwm_enabled_flag; }
static void pwm_status(CLI_PwmStatus *v) { *v = (CLI_PwmStatus){1u, 2u, 3u, 4u}; }
static void pwm_set(uint16_t a, uint16_t b, uint32_t c, uint8_t d) { (void)a; (void)b; (void)c; (void)d; }
static void pwm_dump(CLI_PwmDump *v) { *v = (CLI_PwmDump){1u, 2u, 3u, 4u, 5u, 6u}; }
static void pwm_full(uint32_t out[22]) { unsigned i; for (i = 0u; i < 22u; ++i) out[i] = i; }
static void pwm_sys(uint32_t out[4]) { out[0] = 170000000u; out[1] = 169u; out[2] = 170000000u; out[3] = 0x1234u; }
static int pwm_dt(uint32_t v) { (void)v; return pwm_dt_rc; }
static uint32_t pwm_dtreg(void) { return 12u; }
static int foc_start(void) { return foc_start_rc; }
static void foc_stop(void) { }
static int foc_running(void) { return foc_running_flag; }
static void foc_speed(int32_t v) { last_speed = v; }
static int32_t foc_get_speed(void) { return last_speed; }
static void foc_current(int32_t a, int32_t b) { last_id = a; last_iq = b; }
static int foc_pp(int32_t p) { last_pp = p; return foc_pp_rc; }
static int foc_vdc(int32_t v) { last_vdc = v; return foc_vdc_rc; }
static int foc_base(int32_t v) { last_base = v; return foc_base_rc; }
static int foc_params(int32_t a, int32_t b, int32_t c) { (void)a; (void)b; (void)c; return foc_set_params_rc; }
static void foc_get_params(int32_t *a, int32_t *b, int32_t *c, int32_t *d) { if (a) *a = 0; if (b) *b = 0; if (c) *c = 7; if (d) *d = 8; }
static int32_t foc_lsig(void) { return 9; }
static int foc_pi(int32_t a, int32_t b) { (void)a; (void)b; return foc_set_pi_rc; }
static int foc_applied(void) { return 1; }
static int32_t foc_vbus(void) { return 24000; }
static int fault(void) { return fault_active; }
static int fault_clear(void) { return fault_clear_rc; }
static void emstop(uint8_t *a, uint8_t *b) { *a = 1u; *b = 0u; }
static int vf_start(int32_t v) { (void)v; return vf_rc; }
static void vf_stop(void) { }
static int vf_running(void) { return vf_running_flag; }
static void vf_status(CLI_VfStatus *v) { *v = (CLI_VfStatus){100, 90, 1, 2, 3, 4, 5}; }
static void vf_set(int32_t a, int32_t b) { last_vfk_boost = a; last_vfk_rated = b; }
static void trig(void) { }
static void enc(CLI_EncoderStatus *v) { *v = (CLI_EncoderStatus){123u, -45, 1087u, 543u, 0u}; }
static int8_t at_run(CLI_AutotuneKind k) { last_at_kind = k; return (int8_t)at_rc; }
static void at_abort(uint8_t v) { last_abort = v; }
static void at_void(void) { }
static void at_pi(int32_t v) { (void)v; }
static int at_last(int32_t *a, int32_t *b, int32_t *c) { *a = 1; *b = 2; *c = 3; return at_last_rc; }
static void motor_get(CLI_MotorParams *v) { *v = motor; }
static void motor_set(const CLI_MotorParams *v) { motor = *v; }
static void help(void) { ++help_count; }
static void swo(uint32_t v) { (void)v; ++swo_count; }
static int mapcap(const char *line)
{
    if (!mapcap_enabled) return 0;
    if (strcmp(line, "mcarm=7") == 0) {
        send_telem("@MC:ARM:cap=1:rc=0\r\n> ");
        return 1;
    }
    if (strcmp(line, "mapcap run") == 0) {
        send_telem("@MC:RUN:rc=0\r\n> ");
        return 1;
    }
    if (strcmp(line, "mapcap drain") == 0) {
        send_telem("@MC:DRAIN:records=0\r\n> ");
        return 1;
    }
    if (strcmp(line, "mapcap build=7") == 0) {
        send_telem("@MAP:READY:records=0:rows=0\r\n> ");
        return 1;
    }
    if (strcmp(line, "mapcap abort") == 0) {
        send_telem("@MC:ABORT:rc=0\r\n> ");
        return 1;
    }
    if (strcmp(line, "mapcap status") == 0) {
                send_telem("@MC:STATUS:state=0:term=0:cap=0:frames=0:dropped=0:periods=0:avail=0:detail=0:raw_vbus=0:vbus_mv=0:i1_ma=0:i2_ma=0:adc_status=0:sector=0:window=0\r\n> ");

        return 1;
    }
    return 0;
}

int main(void)
{
    CLI_Ops o = {
        .send = send_text, .send_telem = send_telem, .send_dbg = send_dbg, .send_dbg_fmt = send_dbg_fmt,
        .print_help = help, .swo_test = swo, .tick_ms = tick_ms,
        .adc_start = adc_start, .adc_raw = adc_raw, .adc_offsets = adc_offsets, .adc_calibrate_256 = adc_cal, .adc_calibrate = adc_cal,
        .adc_irq_disable = irq_off, .adc_irq_enable = irq_on, .adc_diag = adc_diag, .adc_counts = adc_counts,
        .pwm_is_enabled = pwm_enabled, .pwm_status = pwm_status, .pwm_set_debug = pwm_set, .pwm_dump = pwm_dump, .pwm_dump8 = pwm_dump,
        .pwm_full_dump = pwm_full, .pwm_sysinfo = pwm_sys, .pwm_set_deadtime = pwm_dt, .pwm_deadtime_reg = pwm_dtreg,
        .foc_start = foc_start, .foc_stop = foc_stop, .foc_is_running = foc_running, .foc_set_speed = foc_speed, .foc_get_speed = foc_get_speed,
        .foc_set_current = foc_current, .foc_set_pole_pairs = foc_pp, .foc_set_vdc_mv = foc_vdc, .foc_set_base_speed = foc_base,
        .foc_set_params = foc_params, .foc_get_params = foc_get_params, .foc_sigma_l = foc_lsig, .foc_set_pi = foc_pi,
        .foc_params_applied = foc_applied, .foc_vbus_mv = foc_vbus,
        .fault_is_active = fault, .fault_reason = fault, .fault_request_clear = fault_clear,
        .em_stop_state = emstop,
        .vf_start = vf_start, .vf_stop = vf_stop, .vf_is_running = vf_running, .vf_status = vf_status, .vf_set_params = vf_set,
        .trig_high = trig, .trig_low = trig, .encoder_status = enc,
        .autotune_run = at_run, .autotune_abort_set = at_abort, .autotune_print_curve = at_void, .autotune_print_params = at_void,
        .autotune_print_stats = at_void, .autotune_calc_pi = at_pi, .autotune_last_pi = at_last,
        .motor_get = motor_get, .motor_set = motor_set, .mapcap_command = mapcap
    };
    CLI_State s = {0};
    int rc;

    reset_controls();
    reset_output(); rc = CLI_ProcessLine("a", &o, &s); expect_uart("a", rc, 1, "@ADC:I1=1:I2=2:Ires=3:VBUS=4\r\n> ");
    reset_output(); rc = CLI_ProcessLine("a=0", &o, &s); expect_dbg("a=0", rc, 1, "ADC stream stopped\r\n> ");
    reset_output(); rc = CLI_ProcessLine("a=50", &o, &s); expect_dbg("a=50", rc, 1, "ADC stream started: 50 ms\r\n> "); check("a=50 state", s.adc_stream_period_ms == 50u);
    reset_output(); rc = CLI_ProcessLine("a=49", &o, &s); expect_dbg("a=49", rc, 1, "err: N must be 0 or 50..1000\r\n> ");
    reset_output(); rc = CLI_ProcessLine("a=1001", &o, &s); expect_dbg("a=1001", rc, 1, "err: N must be 0 or 50..1000\r\n> ");
    reset_output(); rc = CLI_ProcessLine("a?", &o, &s); expect_uart("a?", rc, 1, "@ADC:STATUS:offset_i1=11:stream=50\r\n> ");

    reset_output(); rc = CLI_ProcessLine("c", &o, &s); expect_uart("c", rc, 1, "@ADC:CAL:offset_i1=11:offset_i2=12:offset_ires=13\r\n> "); check("c IRQ pair", irq_disable_count == irq_enable_count && adc_cal_count == 1);
    pwm_enabled_flag = 1u; reset_output(); rc = CLI_ProcessLine("c", &o, &s); expect_uart("c PWM guard", rc, 1, "err: PWM running — stop FOC/Vf first\r\n> "); pwm_enabled_flag = 0u;
    reset_output(); rc = CLI_ProcessLine("p?", &o, &s); expect_uart("p?", rc, 1, "@PWM:CR1=1:CCER=2:BDTR=3:CNT=4\r\n> ");
    reset_output(); rc = CLI_ProcessLine("p=99,15,1500", &o, &s); expect_uart("p= optional mask", rc, 1, "@PWM:OK:arr=99:duty=15:dt=1500\r\n> ");
    reset_output(); rc = CLI_ProcessLine("p=99,15,1500,0", &o, &s); expect_uart("p= explicit zero mask", rc, 1, "@PWM:OK:arr=99:duty=15:dt=1500\r\n> ");
    pwm_enabled_flag = 1u; reset_output(); rc = CLI_ProcessLine("p=99,15,1500", &o, &s); expect_uart("p= PWM guard", rc, 1, "err: PWM running — stop FOC/Vf first\r\n> "); pwm_enabled_flag = 0u;

    fault_active = 1; reset_output(); rc = CLI_ProcessLine("1", &o, &s); expect_dbg("1 fault guard", rc, 1, "FAULT! send 'f' to clear\r\n> ");
    fault_active = 0; foc_start_rc = 0; reset_output(); rc = CLI_ProcessLine("1", &o, &s); expect_dbg("1 start", rc, 1, "FOC started\r\n> ");
    foc_start_rc = -2; reset_output(); rc = CLI_ProcessLine("1", &o, &s); expect_uart("1 fail closed", rc, 1, "@FOC:START:FAIL:rc=-2 (0=OK -1=clock/fault -2=map_unverified -3=calib -4=arm)\r\n> ");
    reset_output(); rc = CLI_ProcessLine("0", &o, &s); expect_dbg("0", rc, 1, "FOC stopped\r\n> ");
    reset_output(); rc = CLI_ProcessLine("m", &o, &s); check("m help", rc == 1 && help_count == 1u && uart_out[0] == '\0' && dbg_out[0] == '\0');
    reset_output(); rc = CLI_ProcessLine("s", &o, &s); expect_uart("s", rc, 1, "SWO test sent\r\n> "); check("s SWO", swo_count == 1u);

    foc_running_flag = 1; reset_output(); rc = CLI_ProcessLine("f", &o, &s); check("f control active", rc == 1 && strcmp(dbg_out, "@FAULT:CLEAR:STATUS=2:em_stop1=1:em_stop2=0\r\n> err: stop FOC/Vf first\r\n> ") == 0); foc_running_flag = 0;
    fault_clear_rc = 0; reset_output(); rc = CLI_ProcessLine("f", &o, &s); expect_dbg("f clear", rc, 1, "fault cleared\r\n> ");
    fault_clear_rc = 4; reset_output(); rc = CLI_ProcessLine("f", &o, &s); check("f reject", rc == 1 && strcmp(dbg_out, "@FAULT:CLEAR:STATUS=4:em_stop1=1:em_stop2=0\r\n> fault NOT cleared: Vbus/current still out of range\r\n> ") == 0); fault_clear_rc = 0;
    reset_output(); rc = CLI_ProcessLine("s=500", &o, &s); expect_dbg("s= valid", rc, 1, "speed=500 rpm\r\n> ");
    reset_output(); rc = CLI_ProcessLine("s=500x", &o, &s); expect_uart("s= trailing", rc, 1, "err: trailing chars\r\n> ");
    reset_output(); rc = CLI_ProcessLine("s=50001", &o, &s); expect_uart("s= range", rc, 1, "err: out of range\r\n> ");
    reset_output(); rc = CLI_ProcessLine("s=x", &o, &s); expect_uart("s= no digits", rc, 1, "err: no digits\r\n> ");

    reset_output(); rc = CLI_ProcessLine("dump", &o, &s); expect_uart("dump", rc, 1, "@PWM:DUMP:PSC=1:ARR=2:BDTR=0x00000003:CR1=0x00000004:CR2=0x00000005:CCER=0x00000006\r\n> ");
    reset_output(); rc = CLI_ProcessLine("dumpa", &o, &s); expect_uart("dumpa", rc, 1, "@ADUMP:SQR1=0x00000000:CFGR=0x00000001:SMPR1=0x00000002:JSQR=0x00000003:DIFSEL=0x00000004:CR=0x00000005:ISR=0x00000006:DR=0x0007:JDR1=0x0008:JDR2=0x0009:JDR3=0x000A:JDR4=0x000B:ADC1_CR=0x0000000C:ADC1_ISR=0x0000000D\r\n> ");
    reset_output(); rc = CLI_ProcessLine("dump8", &o, &s); expect_uart("dump8", rc, 1, "@PWM8:DUMP:PSC=1:ARR=2:BDTR=0x00000003:CR1=0x00000004:CR2=0x00000005:CCER=0x00000006\r\n> ");
    reset_output(); rc = CLI_ProcessLine("pdump", &o, &s); expect_uart("pdump", rc, 1, "@PWM:FULL:SYS=0:CFGR=0x00000001:T1:PSC=2:ARR=3:CCR=4,5,6:BDTR=0x00000007:CCER=0x00000008:CR1=0x00000009:CNT=10:T8:PSC=11:ARR=12:CCR=13,14,15:BDTR=0x00000010:CCER=0x00000011:CR1=0x00000012:CNT=19\r\n> ");
    reset_output(); rc = CLI_ProcessLine("sysinfo", &o, &s); expect_uart("sysinfo", rc, 1, "@SYS:CLK=170000000:PSC=169:TCLK=170000000:PLLCFGR=0x00001234:OVR=1:JEOS=2:TO=3:JQOVF=4\r\n> ");

    reset_output(); rc = CLI_ProcessLine("pp=4", &o, &s); expect_uart("pp valid", rc, 1, "pole_pairs=4\r\n> "); check("pp applied", last_pp == 4 && motor.pairs == 4);
    reset_output(); rc = CLI_ProcessLine("pp=0", &o, &s); expect_uart("pp range", rc, 1, "err: pole pairs must be 1..24\r\n> ");
    foc_running_flag = 1; reset_output(); rc = CLI_ProcessLine("pp=4", &o, &s); expect_uart("pp running guard", rc, 1, "err: stop FOC/Vf first\r\n> "); foc_running_flag = 0;
    foc_pp_rc = -1; reset_output(); rc = CLI_ProcessLine("pp=4", &o, &s); expect_uart("pp reject", rc, 1, "err: pole pairs not applied\r\n> "); foc_pp_rc = 0;
    reset_output(); rc = CLI_ProcessLine("vdc=48", &o, &s); expect_uart("vdc valid", rc, 1, "@VDC:OK:48000 mV (VBUS measured=24000 mV)\r\n> "); check("vdc applied", last_vdc == 48000);
    reset_output(); rc = CLI_ProcessLine("vdc=9", &o, &s); expect_uart("vdc range", rc, 1, "err: VDC must be 10..400 V\r\n> ");
    foc_vdc_rc = -1; reset_output(); rc = CLI_ProcessLine("vdc=48", &o, &s); expect_uart("vdc reject", rc, 1, "err: VDC not set\r\n> "); foc_vdc_rc = 0;
    reset_output(); rc = CLI_ProcessLine("fwbase=1000", &o, &s); expect_uart("fwbase valid", rc, 1, "fw_base_speed=1000 rpm\r\n> "); check("fwbase applied", last_base == 1000);
    reset_output(); rc = CLI_ProcessLine("fwbase=99", &o, &s); expect_uart("fwbase range", rc, 1, "err: FW base speed must be 100..5000 rpm\r\n> ");
    foc_base_rc = -1; reset_output(); rc = CLI_ProcessLine("fwbase=1000", &o, &s); expect_uart("fwbase reject", rc, 1, "err: can't set base speed\r\n> "); foc_base_rc = 0;
    reset_output(); rc = CLI_ProcessLine("dt=12700", &o, &s); expect_uart("dt valid", rc, 1, "@PWM:DT=12700 ns (DTG=12)\r\n> ");
    reset_output(); rc = CLI_ProcessLine("dt=12701", &o, &s); expect_uart("dt range", rc, 1, "err: max 12700 ns\r\n> ");
    pwm_dt_rc = -1; reset_output(); rc = CLI_ProcessLine("dt=12700", &o, &s); expect_uart("dt guard", rc, 1, "err: PWM running — stop FOC/Vf first\r\n> "); pwm_dt_rc = 0;

    reset_output(); rc = CLI_ProcessLine("curve", &o, &s); expect_uart("curve", rc, 1, "\r\n> ");
    reset_output(); rc = CLI_ProcessLine("params", &o, &s); expect_uart("params", rc, 1, "@AP:1\r\n> ");
    reset_output(); rc = CLI_ProcessLine("irot", &o, &s); expect_uart("irot", rc, 1, "@IROT:OK\r\n> "); check("irot kind", last_at_kind == CLI_AT_IROT);
    at_rc = -1; reset_output(); rc = CLI_ProcessLine("inertia", &o, &s); expect_uart("inertia fail", rc, 1, "@INERTIA:FAIL\r\n> "); at_rc = 0;
    reset_output(); rc = CLI_ProcessLine("ch", &o, &s); expect_uart("ch", rc, 1, "@AT:CH:OK\r\n> "); check("ch IRQ pair", irq_disable_count == irq_enable_count);
    reset_output(); rc = CLI_ProcessLine("chu", &o, &s); expect_uart("chu", rc, 1, "@AT:CHu:OK\r\n> ");
    reset_output(); rc = CLI_ProcessLine("chv", &o, &s); expect_uart("chv", rc, 1, "@AT:CHv:OK\r\n> ");
    at_rc = -1; reset_output(); rc = CLI_ProcessLine("chw", &o, &s); expect_uart("chw fail", rc, 1, "@AT:CHP:FAIL\r\n> "); at_rc = 0;
    reset_output(); rc = CLI_ProcessLine("iv", &o, &s); expect_uart("iv", rc, 1, "@AT:IV:OK\r\n> ");
    reset_output(); rc = CLI_ProcessLine("pairs", &o, &s); expect_uart("pairs", rc, 1, "@AT:PAIRS:RESULT_OK\r\n> ");
    reset_output(); rc = CLI_ProcessLine("abort", &o, &s); expect_uart("abort", rc, 1, "abort requested\r\n> "); check("abort flag", last_abort == 1u);
    at_rc = -5; reset_output(); rc = CLI_ProcessLine("oew", &o, &s); expect_uart("oew aborted", rc, 1, "@AT:OEW:ABORTED\r\n> "); check("oew resets abort", last_abort == 0u);
    at_rc = -6; reset_output(); rc = CLI_ProcessLine("rr", &o, &s); expect_uart("rr aborted", rc, 1, "@AT:RR:ABORTED\r\n> ");
    reset_output(); rc = CLI_ProcessLine("noload", &o, &s); expect_uart("noload aborted", rc, 1, "@AT:NOLOAD:ABORTED\r\n> ");
    at_rc = -1; reset_output(); rc = CLI_ProcessLine("scope", &o, &s); expect_uart("scope fail", rc, 1, "@SCOPE:RESULT_FAIL\r\n> ");
    at_rc = 0; reset_output(); rc = CLI_ProcessLine("pi=250", &o, &s); expect_uart("pi", rc, 1, "> ");
    at_rc = -5; reset_output(); rc = CLI_ProcessLine("lspos", &o, &s); expect_uart("lspos aborted", rc, 1, "@AT:LSPOS:ABORTED\r\n> ");
    at_rc = -5; reset_output(); rc = CLI_ProcessLine("idle", &o, &s); expect_uart("idle aborted", rc, 1, "@IDLE:ABORTED\r\n> "); at_rc = 0;

    reset_output(); rc = CLI_ProcessLine("mp=12,450", &o, &s); expect_uart("mp two args", rc, 1, "@MP:OK:Rs=12:Ls=450:Rr=0:Lm=0:Tr=0:Ke=0:p=0:J=0:Kp=7:Ki=8:Lsig=9:AP=1\r\n> ");
    reset_controls(); reset_output(); rc = CLI_ProcessLine("mp=12,450,13,700,30,40,4,5", &o, &s); expect_uart("mp eight args", rc, 1, "@MP:OK:Rs=12:Ls=450:Rr=13:Lm=700:Tr=30:Ke=40:p=4:J=5:Kp=7:Ki=8:Lsig=9:AP=1\r\n> "); check("mp masks", motor.measured_mask == 0x1DFu);
    foc_set_params_rc = -3; reset_output(); rc = CLI_ProcessLine("mp=12,450", &o, &s); expect_uart("mp fail", rc, 1, "@MP:ERROR:-3\r\n> "); foc_set_params_rc = 0;
    reset_output(); rc = CLI_ProcessLine("mpapply", &o, &s); expect_uart("mpapply", rc, 1, "@MPAPPLY:OK:Rs=12:Ls=450:Rr=13:Lm=700:Tr=30:p=4:Kp=7:Ki=8:Lsig=9:AP=1\r\n> ");
    foc_set_params_rc = -1; reset_output(); rc = CLI_ProcessLine("mpapply", &o, &s); expect_uart("mpapply fail", rc, 1, "@MPAPPLY:ERROR:-1\r\n> "); foc_set_params_rc = 0;
    reset_output(); rc = CLI_ProcessLine("piapply", &o, &s); expect_uart("piapply", rc, 1, "@PI:APPLIED:Kp=1:Ki=2:AP=1\r\n> ");
    at_last_rc = -1; reset_output(); rc = CLI_ProcessLine("piapply", &o, &s); expect_uart("piapply missing", rc, 1, "@PI:ERROR:NOT_CALCULATED\r\n> "); at_last_rc = 0;
    foc_set_pi_rc = -2; reset_output(); rc = CLI_ProcessLine("piapply", &o, &s); expect_uart("piapply reject", rc, 1, "@PI:ERROR:-2\r\n> "); foc_set_pi_rc = 0;
    reset_output(); rc = CLI_ProcessLine("stats", &o, &s); expect_uart("stats", rc, 1, "> ");
    reset_output(); rc = CLI_ProcessLine("i=100,-200", &o, &s); expect_uart("i", rc, 1, "@I:OK:Id=100:Iq=-200\r\n> "); check("i applied", last_id == 100 && last_iq == -200);

    reset_output(); rc = CLI_ProcessLine("vf=0", &o, &s); expect_uart("vf stop", rc, 1, "V/f stopped\r\n> "); check("vf stop log", s.vflog_period_ms == 0u);
    reset_output(); rc = CLI_ProcessLine("vf=5001", &o, &s); expect_uart("vf range", rc, 1, "err: rpm range -5000..+5000\r\n> ");
    fault_active = 1; reset_output(); rc = CLI_ProcessLine("vf=1000", &o, &s); expect_uart("vf fault", rc, 1, "FAULT! send 'f' to clear\r\n> "); fault_active = 0;
    vf_rc = -7; reset_output(); rc = CLI_ProcessLine("vf=5000", &o, &s); expect_uart("vf exit", rc, CLI_EXIT_LOOP, "V/f blocked: rc=-7 (sample context unverified)\r\n> ");
    vf_rc = 0; tick_now = 123u; reset_output(); rc = CLI_ProcessLine("vf=1000", &o, &s); expect_uart("vf start", rc, 1, "V/f started: 1000 rpm\r\n@TRIG:tick=123\r\n> "); check("vf default log", s.vflog_period_ms == CLI_VFLOG_DEFAULT_PERIOD_MS);
    reset_output(); rc = CLI_ProcessLine("vflog=0", &o, &s); expect_uart("vflog stop", rc, 1, "vflog stopped\r\n> ");
    reset_output(); rc = CLI_ProcessLine("vflog=50", &o, &s); expect_uart("vflog start", rc, 1, "vflog started: 50 ms\r\n> "); check("vflog state", s.vflog_period_ms == 50u);
    reset_output(); rc = CLI_ProcessLine("vflog=9", &o, &s); expect_uart("vflog range", rc, 1, "err: N must be 0 or 10..1000\r\n> ");
    reset_output(); rc = CLI_ProcessLine("vf?", &o, &s); expect_uart("vf?", rc, 1, "@VF:target=100:meas=90:fe=1:fslip=2:vmag=3\r\n> ");
    reset_output(); rc = CLI_ProcessLine("enc", &o, &s); expect_uart("enc", rc, 1, "@ENC:angle=123:speed=-45:period_us=1087:pulse_us=543:err=0\r\n> ");
    reset_output(); rc = CLI_ProcessLine("vfk=5,50", &o, &s); expect_uart("vfk", rc, 1, "V/f params: boost=4% rated=5Hz\r\n> "); check("vfk applied", last_vfk_boost == 5 && last_vfk_rated == 50);

    reset_output(); rc = CLI_ProcessLine("mapcap status", &o, &s); expect_uart("mapcap disabled", rc, 0, "unknown\r\n> ");
    mapcap_enabled = 1u;
    reset_output(); rc = CLI_ProcessLine("mcarm=7", &o, &s); expect_uart("mcarm adapter", rc, 1, "@MC:ARM:cap=1:rc=0\r\n> ");
    reset_output(); rc = CLI_ProcessLine("mapcap run", &o, &s); expect_uart("mapcap run adapter", rc, 1, "@MC:RUN:rc=0\r\n> ");
    reset_output(); rc = CLI_ProcessLine("mapcap drain", &o, &s); expect_uart("mapcap drain adapter", rc, 1, "@MC:DRAIN:records=0\r\n> ");
    reset_output(); rc = CLI_ProcessLine("mapcap build=7", &o, &s); expect_uart("mapcap build adapter", rc, 1, "@MAP:READY:records=0:rows=0\r\n> ");
    reset_output(); rc = CLI_ProcessLine("mapcap abort", &o, &s); expect_uart("mapcap abort adapter", rc, 1, "@MC:ABORT:rc=0\r\n> ");
        reset_output(); rc = CLI_ProcessLine("mapcap status", &o, &s); expect_uart("mapcap status adapter", rc, 1, "@MC:STATUS:state=0:term=0:cap=0:frames=0:dropped=0:periods=0:avail=0:detail=0:raw_vbus=0:vbus_mv=0:i1_ma=0:i2_ma=0:adc_status=0:sector=0:window=0\r\n> ");

    reset_output(); rc = CLI_ProcessLine("unknown", &o, &s); expect_uart("unknown", rc, 0, "unknown\r\n> ");
    check("null args", CLI_ProcessLine(0, &o, &s) == -1 && CLI_ProcessLine("a", 0, &s) == -1 && CLI_ProcessLine("a", &o, 0) == -1);

    printf("CLI golden snapshots: %d checks, %d failures\n", checks, failures);
    return failures != 0;
}
