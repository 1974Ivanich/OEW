#include "cli.h"
#include <stdio.h>
#include <string.h>

static char output[512];
static int fault;
static int start_rc = -2;
static int vf_rc = -7;
static int params_rc;
static int calls;

static void send_text(const char *text) { (void)snprintf(output + strlen(output), sizeof(output) - strlen(output), "%s", text); }
static int foc_start(void) { ++calls; return start_rc; }
static void foc_stop(void) { ++calls; }
static int params(int32_t rs, int32_t ls, int32_t vbus) { (void)rs; (void)ls; (void)vbus; return params_rc; }
static int apply(void) { return params_rc; }
static int fault_active(void) { return fault; }
static void clear_fault(void) { fault = 0; }
static int vf_start(int32_t rpm) { (void)rpm; return vf_rc; }
static void vf_stop(void) { ++calls; }
static int pwm_set(uint16_t arr, uint16_t duty, uint32_t dt, uint8_t mask) { (void)arr; (void)duty; (void)dt; return mask == 0 ? 0 : -1; }
static void pwm_status(uint32_t *a, uint32_t *b, uint32_t *c, uint32_t *d) { *a=1; *b=2; *c=3; *d=4; }
static int enc_status(uint16_t *a, int32_t *s, uint32_t *p, uint32_t *w, uint8_t *e) { *a=123; *s=-45; *p=1087; *w=543; *e=0; return 0; }
static void reset_output(void) { output[0] = '\0'; }
static void check(const char *name, int condition) { static int checks, failures; ++checks; if (!condition) { ++failures; printf("FAIL: %s\n", name); } else printf("ok:   %s\n", name); if (strcmp(name, "unknown") == 0) printf("CLI: %d checks, %d failures\n", checks, failures); }

int main(void)
{
    CLI_Ops ops = {send_text, 0, foc_start, foc_stop, params, apply, fault_active, clear_fault, vf_start, vf_stop, pwm_set, pwm_status, enc_status};
    CLI_State state = {0};
    reset_output(); CLI_ProcessLine("p=99,15,1500,0", &ops, &state); check("p command exact", strcmp(output, "@PWM:OK:arr=99:duty=15:dt=1500\r\n> ") == 0);
    reset_output(); CLI_ProcessLine("p?", &ops, &state); check("p? exact", strcmp(output, "@PWM:CR1=1:CCER=2:BDTR=3:CNT=4\r\n> ") == 0);
    reset_output(); CLI_ProcessLine("1", &ops, &state); check("FOC fail-closed response", strstr(output, "@FOC:START:FAIL:rc=-2") != 0);
    fault = 1; reset_output(); CLI_ProcessLine("1", &ops, &state); check("fault blocks FOC", strcmp(output, "FAULT! send 'f' to clear\r\n> ") == 0);
    reset_output(); CLI_ProcessLine("f", &ops, &state); check("fault clear", strcmp(output, "fault cleared\r\n> ") == 0 && fault == 0);
    params_rc = 0; reset_output(); CLI_ProcessLine("mp=12,450,24000", &ops, &state); check("mp success", strcmp(output, "@MP:OK\r\n> ") == 0 && state.params_valid);
    vf_rc = -7; reset_output(); CLI_ProcessLine("vf=1000", &ops, &state); check("vf blocked exact", strstr(output, "V/f blocked: rc=-7") != 0);
    reset_output(); CLI_ProcessLine("vf=0", &ops, &state); check("vf stopped exact", strcmp(output, "V/f stopped\r\n> ") == 0);
    reset_output(); CLI_ProcessLine("enc", &ops, &state); check("enc exact", strcmp(output, "@ENC:angle=123:speed=-45:period_us=1087:pulse_us=543:err=0\r\n> ") == 0);
    reset_output(); CLI_ProcessLine("xyz", &ops, &state); check("unknown", strcmp(output, "unknown\r\n> ") == 0);
    return 0;
}
