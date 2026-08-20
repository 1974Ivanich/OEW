#ifndef OEW_CLI_H
#define OEW_CLI_H

#include <stdint.h>

typedef struct {
    void (*send)(const char *text);
    void (*send_telem)(const char *fmt, ...);
    int (*foc_start)(void);
    void (*foc_stop)(void);
    int (*foc_set_params)(int32_t rs, int32_t ls, int32_t vbus);
    int (*foc_apply_params)(void);
    int (*fault_is_active)(void);
    void (*fault_clear)(void);
    int (*vf_start)(int32_t rpm);
    void (*vf_stop)(void);
    int (*pwm_set)(uint16_t arr, uint16_t duty, uint32_t deadtime, uint8_t mask);
    void (*pwm_status)(uint32_t *cr1, uint32_t *ccer, uint32_t *bdtr, uint32_t *cnt);
    int (*encoder_status)(uint16_t *angle, int32_t *speed, uint32_t *period,
                          uint32_t *pulse, uint8_t *error);
} CLI_Ops;

typedef struct {
    int32_t motor_rs;
    int32_t motor_ls;
    int32_t motor_vbus;
    uint8_t params_valid;
} CLI_State;

int CLI_ProcessLine(const char *line, const CLI_Ops *ops, CLI_State *state);

#endif
