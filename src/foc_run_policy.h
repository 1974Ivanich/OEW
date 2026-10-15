#ifndef FOC_RUN_POLICY_H
#define FOC_RUN_POLICY_H

#include <stdbool.h>
#include <stdint.h>

/* P1 control-policy constants. Five FOC cycles are 1 ms at 5 kHz: this
 * suppresses a single corrupted encoder sample while remaining much shorter
 * than the 5 ms encoder-input timeout. */
#define FOC_RUN_POLICY_ENCODER_BAD_CYCLES       5u
#define FOC_RUN_POLICY_REVERSAL_ZERO_RPM        30
#define FOC_RUN_POLICY_REVERSAL_ZERO_CYCLES     5u

/* Пока привод не показал ни одного ненулевого отсчёта скорости, нулевая
 * скорость — это разгон с места, а не потеря датчика: энкодер 14 бит при
 * кадре ~896 мкс читает РОВНО НОЛЬ ниже ~16 об/мин, поэтому критерий
 * «сырая скорость == 0 при ненулевой уставке» на старте срабатывал через
 * 5 циклов (1 мс) и снимал ШИМ, не дав машине тронуться (замер 15.10.2026:
 * прямой старт умирал за 62 мс, `bad` при этом не рос — сигнал был здоров).
 * Критерий взводится после первого ненулевого отсчёта; если вращения нет
 * дольше этого окна, это уже отказ старта — привод останавливается.
 * Значение ПРЕДВАРИТЕЛЬНОЕ (5 с при 5 кГц): момент инерции машины не измерен,
 * уточнить по измеренному времени первого успешного разгона. */
#define FOC_RUN_POLICY_STARTUP_CYCLES            25000u

typedef enum {
    FOC_RUN_POLICY_OK = 0,
    FOC_RUN_POLICY_ENCODER_LOST
} FocRunPolicyResult;

typedef struct {
    uint8_t encoder_bad_cycles;
    uint8_t reversal_zero_cycles;
    bool reversal_pending;
    int32_t pending_reverse_rpm;
    bool speed_seen;            /* взведение критерия нулевой скорости: было вращение */
    uint32_t startup_cycles;    /* циклы RUN без единого ненулевого отсчёта скорости */
} FocRunPolicy;

void FocRunPolicy_Init(FocRunPolicy *policy);

/* Accept a requested speed. In RUN, an opposite nonzero sign is not applied
 * immediately: output target becomes zero and the requested target is held
 * until the measured shaft speed is stably close to zero. Returns true only
 * when a reversal was queued. */
bool FocRunPolicy_RequestSpeed(FocRunPolicy *policy,
                               int32_t *active_speed_rpm,
                               int32_t measured_speed_rpm,
                               int32_t requested_rpm,
                               bool in_run);

/* Advance the runtime policy once per FOC cycle. Encoder errors are always
 * fatal after debounce. A zero speed is fatal only while nonzero motion is
 * expected; it is deliberately accepted during a commanded reversal or an
 * explicit zero-speed command. */
FocRunPolicyResult FocRunPolicy_Update(FocRunPolicy *policy,
                                       int32_t *active_speed_rpm,
                                       int32_t encoder_raw_rpm,
                                       uint8_t encoder_error);

bool FocRunPolicy_IsReversalPending(const FocRunPolicy *policy);

#endif
