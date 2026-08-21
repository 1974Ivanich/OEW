#include "cli.h"
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

static char uart_out[2048], dbg_out[2048];
static int failures, checks, irq_disable_count, irq_enable_count, at_rc, vf_rc, fault_active;
static uint32_t tick_now = 123u;
static CLI_MotorParams motor = { 10, 500, 20, 700, 30, 40, 4, 5, 0u };

static void append(char *out, const char *fmt, va_list ap) { size_t n=strlen(out); (void)vsnprintf(out+n, 2048u-n, fmt, ap); }
static void send_text(const char *s) { strncat(uart_out,s,sizeof(uart_out)-strlen(uart_out)-1u); }
static void send_telem(const char *fmt, ...) { va_list ap; va_start(ap,fmt); append(uart_out,fmt,ap); va_end(ap); }
static void send_dbg(const char *s) { strncat(dbg_out,s,sizeof(dbg_out)-strlen(dbg_out)-1u); }
static void send_dbg_fmt(const char *fmt, ...) { va_list ap; va_start(ap,fmt); append(dbg_out,fmt,ap); va_end(ap); }
static uint32_t tick_ms(void) { return tick_now; }
static void adc_start(void) { }
static void adc_raw(CLI_AdcRaw *v) { *v=(CLI_AdcRaw){1,2,3,4}; }
static void adc_offsets(CLI_AdcOffsets *v) { *v=(CLI_AdcOffsets){11,12,13}; }
static void adc_cal(void) { }
static void irq_off(void) { ++irq_disable_count; }
static void irq_on(void) { ++irq_enable_count; }
static void adc_diag(uint32_t out[12]) { unsigned i; for(i=0;i<12;i++) out[i]=i; }
static void adc_counts(uint32_t out[4]) { out[0]=1;out[1]=2;out[2]=3;out[3]=4; }
static int pwm_enabled(void) { return 0; }
static void pwm_status(CLI_PwmStatus *v) { *v=(CLI_PwmStatus){1,2,3,4}; }
static void pwm_set(uint16_t a,uint16_t b,uint32_t c,uint8_t d) { (void)a;(void)b;(void)c;(void)d; }
static void pwm_dump(CLI_PwmDump *v) { *v=(CLI_PwmDump){1,2,3,4,5,6}; }
static void pwm_full(uint32_t out[22]) { unsigned i; for(i=0;i<22;i++)out[i]=i; }
static void pwm_sys(uint32_t out[3]) { out[0]=170000000u;out[1]=169u;out[2]=170000000u; }
static int pwm_dt(uint32_t v) { (void)v; return 0; }
static uint32_t pwm_dtreg(void) { return 12u; }
static int foc_start(void) { return -2; }
static void foc_stop(void) { }
static int foc_running(void) { return 0; }
static void foc_speed(int32_t v) { (void)v; }
static int32_t foc_get_speed(void) { return 123; }
static void foc_current(int32_t a,int32_t b) { (void)a;(void)b; }
static int foc_pp(uint8_t p) { (void)p;return 0; }
static int foc_vdc(int32_t v) { (void)v;return 0; }
static int foc_base(int32_t v) { (void)v;return 0; }
static int foc_params(int32_t a,int32_t b,int32_t c) { (void)a;(void)b;(void)c;return 0; }
static void foc_get_params(int32_t*a,int32_t*b,int32_t*c,int32_t*d){if(a)*a=0;if(b)*b=0;if(c)*c=7;if(d)*d=8;}
static int32_t foc_lsig(void) { return 9; }
static int foc_pi(int32_t a,int32_t b) { (void)a;(void)b;return 0; }
static int foc_applied(void) { return 1; }
static int32_t foc_vbus(void) { return 24000; }
static int fault(void) { return fault_active; }
static int fault_clear(void) { return 0; }
static int vf_start(int32_t v) { (void)v;return vf_rc; }
static void vf_stop(void) { }
static int vf_running(void) { return 0; }
static void vf_status(CLI_VfStatus *v){*v=(CLI_VfStatus){100,90,1,2,3,4,5};}
static void vf_set(int32_t a,int32_t b){(void)a;(void)b;}
static void trig(void) { }
static void enc(CLI_EncoderStatus *v){*v=(CLI_EncoderStatus){123,-45,1087,543,0};}
static int8_t at_run(CLI_AutotuneKind k){(void)k;return (int8_t)at_rc;}
static void at_abort(uint8_t v){(void)v;}
static void at_void(void) { }
static void at_pi(int32_t v){(void)v;}
static int at_last(int32_t*a,int32_t*b,int32_t*c){*a=1;*b=2;*c=3;return 0;}
static void motor_get(CLI_MotorParams *v){*v=motor;}
static void motor_set(const CLI_MotorParams *v){motor=*v;}
static void reset(void){uart_out[0]=dbg_out[0]='\0';}
static void check(const char *name,int ok){++checks;if(!ok){++failures;printf("FAIL: %s\n",name);}else printf("ok:   %s\n",name);}

int main(void)
{
    CLI_Ops o = {
        .send=send_text,.send_telem=send_telem,.send_dbg=send_dbg,.send_dbg_fmt=send_dbg_fmt,.tick_ms=tick_ms,
        .adc_start=adc_start,.adc_raw=adc_raw,.adc_offsets=adc_offsets,.adc_calibrate_256=adc_cal,.adc_calibrate=adc_cal,.adc_irq_disable=irq_off,.adc_irq_enable=irq_on,.adc_diag=adc_diag,.adc_counts=adc_counts,
        .pwm_is_enabled=pwm_enabled,.pwm_status=pwm_status,.pwm_set_debug=pwm_set,.pwm_dump=pwm_dump,.pwm_dump8=pwm_dump,.pwm_full_dump=pwm_full,.pwm_sysinfo=pwm_sys,.pwm_set_deadtime=pwm_dt,.pwm_deadtime_reg=pwm_dtreg,
        .foc_start=foc_start,.foc_stop=foc_stop,.foc_is_running=foc_running,.foc_set_speed=foc_speed,.foc_get_speed=foc_get_speed,.foc_set_current=foc_current,.foc_set_pole_pairs=foc_pp,.foc_set_vdc_mv=foc_vdc,.foc_set_base_speed=foc_base,.foc_set_params=foc_params,.foc_get_params=foc_get_params,.foc_sigma_l=foc_lsig,.foc_set_pi=foc_pi,.foc_params_applied=foc_applied,.foc_vbus_mv=foc_vbus,
        .fault_is_active=fault,.fault_request_clear=fault_clear,.vf_start=vf_start,.vf_stop=vf_stop,.vf_is_running=vf_running,.vf_status=vf_status,.vf_set_params=vf_set,.trig_high=trig,.trig_low=trig,.encoder_status=enc,
        .autotune_run=at_run,.autotune_abort_set=at_abort,.autotune_print_curve=at_void,.autotune_print_params=at_void,.autotune_print_stats=at_void,.autotune_calc_pi=at_pi,.autotune_last_pi=at_last,.motor_get=motor_get,.motor_set=motor_set
    };
    CLI_State s = {0};
    reset(); check("a snapshot",CLI_ProcessLine("a",&o,&s)==1 && strcmp(uart_out,"@ADC:I1=1:I2=2:Ires=3:VBUS=4\r\n> ")==0);
    reset(); check("a stream debug channel",CLI_ProcessLine("a=50",&o,&s)==1 && s.adc_stream_period_ms==50u && strcmp(dbg_out,"ADC stream started: 50 ms\r\n> ")==0);
    reset(); check("c pairs irq",CLI_ProcessLine("c",&o,&s)==1 && irq_disable_count==irq_enable_count && strstr(uart_out,"@ADC:CAL:")!=0);
    reset(); check("p optional mask",CLI_ProcessLine("p=99,15,1500",&o,&s)==1 && strcmp(uart_out,"@PWM:OK:arr=99:duty=15:dt=1500\r\n> ")==0);
    reset(); check("foc fail closed",CLI_ProcessLine("1",&o,&s)==1 && strstr(uart_out,"@FOC:START:FAIL:rc=-2")!=0);
    reset(); check("mp eight fields",CLI_ProcessLine("mp=12,450,13,700,30,40,4,5",&o,&s)==1 && strstr(uart_out,"@MP:OK:Rs=12:Ls=450")!=0 && motor.measured_mask!=0u);
    reset(); at_rc=0; check("autotune irq pair",CLI_ProcessLine("ch",&o,&s)==1 && irq_disable_count==irq_enable_count && strcmp(uart_out,"@AT:CH:OK\r\n> ")==0);
    reset(); vf_rc=-7; check("vf exit loop",CLI_ProcessLine("vf=5000",&o,&s)==CLI_EXIT_LOOP && strstr(uart_out,"V/f blocked: rc=-7")!=0);
    reset(); vf_rc=0; check("vf starts log",CLI_ProcessLine("vf=1000",&o,&s)==1 && s.vflog_period_ms==CLI_VFLOG_DEFAULT_PERIOD_MS && strstr(uart_out,"@TRIG:tick=123")!=0);
    reset(); check("encoder snapshot",CLI_ProcessLine("enc",&o,&s)==1 && strcmp(uart_out,"@ENC:angle=123:speed=-45:period_us=1087:pulse_us=543:err=0\r\n> ")==0);
    reset(); check("unknown",CLI_ProcessLine("xyz",&o,&s)==0 && strcmp(uart_out,"unknown\r\n> ")==0);
    printf("CLI: %d checks, %d failures\n",checks,failures);
    return failures != 0;
}
