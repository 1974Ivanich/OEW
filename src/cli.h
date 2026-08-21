#ifndef OEW_CLI_H
#define OEW_CLI_H

#include <stddef.h>
#include <stdint.h>

#define CLI_EXIT_LOOP (-2)
#define CLI_VFLOG_DEFAULT_PERIOD_MS 20u

typedef enum {
    CLI_AT_IROT, CLI_AT_INERTIA, CLI_AT_CH, CLI_AT_CHU, CLI_AT_CHV, CLI_AT_CHW,
    CLI_AT_IV, CLI_AT_PAIRS, CLI_AT_OEW, CLI_AT_RR, CLI_AT_NOLOAD, CLI_AT_SCOPE,
    CLI_AT_LSPOS, CLI_AT_IDLE
} CLI_AutotuneKind;

typedef struct {
    uint32_t i1, i2, ires, vbus;
} CLI_AdcRaw;
typedef struct {
    uint32_t offset_i1, offset_i2, offset_ires;
} CLI_AdcOffsets;
typedef struct {
    uint32_t cr1, ccer, bdtr, cnt;
} CLI_PwmStatus;
typedef struct {
    uint32_t psc, arr, bdtr, cr1, cr2, ccer;
} CLI_PwmDump;
typedef struct {
    uint16_t angle;
    int32_t speed;
    uint32_t period, pulse;
    uint8_t error;
} CLI_EncoderStatus;
typedef struct {
    int32_t target, measured, fe, slip, vmag, boost, rated;
} CLI_VfStatus;
typedef struct {
    int32_t rs, ls, rr, lm, tr, ke, pairs, inertia;
    uint32_t measured_mask;
} CLI_MotorParams;

typedef struct {
    void (*send)(const char *text);
    void (*send_telem)(const char *fmt, ...);
    void (*send_dbg)(const char *text);
    void (*send_dbg_fmt)(const char *fmt, ...);
    void (*print_help)(void);
    void (*swo_test)(uint32_t tick);
    uint32_t (*tick_ms)(void);

    int (*adc_start)(void);
    void (*adc_raw)(CLI_AdcRaw *out);
    void (*adc_offsets)(CLI_AdcOffsets *out);
    int (*adc_calibrate_256)(void);
    int (*adc_calibrate)(void);
    void (*adc_irq_disable)(void);
    void (*adc_irq_enable)(void);
    void (*adc_diag)(uint32_t out[12]);
    void (*adc_counts)(uint32_t out[4]);

    uint32_t (*pwm_is_enabled)(void);
    void (*pwm_status)(CLI_PwmStatus *out);
    void (*pwm_set_debug)(uint16_t arr, uint16_t duty, uint32_t deadtime, uint8_t mask);
    void (*pwm_dump)(CLI_PwmDump *out);
    void (*pwm_dump8)(CLI_PwmDump *out);
    void (*pwm_full_dump)(uint32_t out[22]);
    void (*pwm_sysinfo)(uint32_t out[4]);
    int (*pwm_set_deadtime)(uint32_t ns);
    uint32_t (*pwm_deadtime_reg)(void);

    int (*foc_start)(void);
    void (*foc_stop)(void);
    int (*foc_is_running)(void);
    void (*foc_set_speed)(int32_t rpm);
    int32_t (*foc_get_speed)(void);
    void (*foc_set_current)(int32_t id, int32_t iq);
    int (*foc_set_pole_pairs)(int32_t pairs);
    int (*foc_set_vdc_mv)(int32_t mv);
    int (*foc_set_base_speed)(int32_t rpm);
    int (*foc_set_params)(int32_t rs, int32_t ls, int32_t vbus);
    void (*foc_get_params)(int32_t *rs, int32_t *ls, int32_t *kp, int32_t *ki);
    int32_t (*foc_sigma_l)(void);
    int (*foc_set_pi)(int32_t kp, int32_t ki);
    int (*foc_params_applied)(void);
    int32_t (*foc_vbus_mv)(void);

    int (*fault_is_active)(void);
    int (*fault_reason)(void);
    int (*fault_request_clear)(void);

    int (*vf_start)(int32_t rpm);
    void (*vf_stop)(void);
    int (*vf_is_running)(void);
    void (*vf_status)(CLI_VfStatus *out);
    void (*vf_set_params)(int32_t boost, int32_t rated);
    void (*trig_high)(void);
    void (*trig_low)(void);

    void (*encoder_status)(CLI_EncoderStatus *out);

    int8_t (*autotune_run)(CLI_AutotuneKind kind);
    void (*autotune_abort_set)(uint8_t value);
    void (*autotune_print_curve)(void);
    void (*autotune_print_params)(void);
    void (*autotune_print_stats)(void);
    void (*autotune_calc_pi)(int32_t hz);
    int (*autotune_last_pi)(int32_t *kp, int32_t *ki, int32_t *bw);

    void (*motor_get)(CLI_MotorParams *out);
    void (*motor_set)(const CLI_MotorParams *in);

    /* Commissioning-only operations return nonzero if they formatted output. */
    int (*mapcap_command)(const char *line);
} CLI_Ops;

typedef struct {
    int32_t motor_rs;
    int32_t motor_ls;
    int32_t motor_vbus;
    uint8_t params_valid;
    uint32_t adc_stream_period_ms;
    uint32_t adc_stream_last_ms;
    uint32_t vflog_period_ms;
    uint32_t vflog_last_ms;
} CLI_State;

int CLI_ProcessLine(const char *line, const CLI_Ops *ops, CLI_State *state);

#endif
