#include "flux_weakening.h"
#include "cordic_math.h"

#ifndef CLAMP
#define CLAMP(x, min, max) ((x) < (min) ? (min) : (x) > (max) ? (max) : (x))
#endif

/* Скорость плавного разматывания FW при выходе из насыщения
 * (единицы id_fw_q15 за цикл 200 мкс). 100 → ~6 мс от -3000 (3А) до 0.
 * Ревью FW-05: вынесено в fw->recovery_step (настраивается; при желании
 * связать с Tr_rotor_us из autotune — выставить recovery_step извне). */
#define FW_RECOVERY_STEP        100

void FW_Init(FluxWeakening *fw, int32_t kp, int32_t ki) {
    /* Ревью FW-04: vdc_mv/v_max_mv/v_threshold удалены — mV-поля были
     * мёртвым грузом; единый источник Vmax — FW_SetVmaxQ15 от VM. */
    fw->v_max_q15 = 0;  /* будет установлен через FW_SetVmaxQ15 от VM */
    fw->kp = kp; fw->ki = ki;
    fw->integrator = 0;
    fw->id_fw_q15 = 0;
    fw->iq_max_q15 = 32767; /* макс */
    fw->active = 0;
    fw->out_max = 0;
    fw->out_min = -32768;   /* FW-02: пересчитывается в FW_Update из id_base */
    fw->recovery_step = FW_RECOVERY_STEP;
    fw->base_speed_rpm = 1000;  /* FW-01: speed gate по умолчанию; задаётся из GUI (fwbase=N) */
    fw->speed_gate = 0;
}

void FW_SetBaseSpeedRpm(FluxWeakening *fw, int32_t rpm) {
    fw->base_speed_rpm = rpm;
}

void FW_SetVmaxQ15(FluxWeakening *fw, int32_t v_max_q15) {
    fw->v_max_q15 = v_max_q15;
}

void FW_Update(FluxWeakening *fw, int32_t vd_q15, int32_t vq_q15,
               int32_t limit_scale_q15, int32_t id_base_q15, int32_t speed_rpm) {
    (void)vq_q15;   /* резерв: q-лимит считается из |vd| и Vmax (см. ниже) */
    /* Ревью FW-06: защита от не-Q15 входов (публичный API). */
    if (vd_q15 < -32767) vd_q15 = -32767;
    if (vd_q15 > 32767) vd_q15 = 32767;
    if (limit_scale_q15 < 0) limit_scale_q15 = 0;
    if (limit_scale_q15 > 32767) limit_scale_q15 = 32767;

    /* Ревью FW-01: speed gate — FW только выше базовой скорости (задаётся
     * из GUI, fwbase=N). Гистерезис 5%: включение при |rpm| ≥ base,
     * отпускание при |rpm| < base·95/100 — не ослабляем поле при
     * перегрузке по моменту/просадке Vbus на низкой скорости. */
    int32_t spd_abs = (speed_rpm >= 0) ? speed_rpm : -speed_rpm;
    if (spd_abs >= fw->base_speed_rpm) fw->speed_gate = 1;
    else if (spd_abs < (int32_t)(((int64_t)fw->base_speed_rpm * 95) / 100)) fw->speed_gate = 0;
    if (!fw->speed_gate) {
        fw->active = 0;
        fw->iq_max_q15 = 32767;
        if (fw->integrator < 0) {
            fw->integrator += fw->recovery_step;
            if (fw->integrator > 0) fw->integrator = 0;
        }
        fw->id_fw_q15 = fw->integrator;  /* p_term=0 при восстановлении */
        return;
    }

    /* Ревью FW-02: нижний предел добавки — НЕ полный Q15 (−32768), а
     * −id_base: суммарный Id (базовый + добавка) не уходит в минус,
     * поток не разворачивается. id_base_q15 — в мА (единицы id_fw). */
    fw->out_min = (id_base_q15 > 0) ? -id_base_q15 : 0;

    /* limit_scale_q15: 32767 = нет насыщения, <32767 = степень ограничения.
     * Используем как основной сигнал для FW: чем глубже насыщение,
     * тем сильнее ослабляем поле (отрицательный Id). */
    if (limit_scale_q15 >= 32767) {
        /* Нет насыщения — ПЛАВНОЕ разматывание FW (ревью Gemini FW).
         * Жёсткий сброс id_fw/integrator = 0 давал дребезг на границе
         * насыщения: FW выключался → поток рос → ЭДС снова упиралась
         * в Vmax → FW включался (автоколебания).
         * Теперь интегратор плавно доезжает до 0 со скоростью
         * FW_RECOVERY_STEP за цикл (200 мкс). При -3А → ~6 мс. */
        fw->active = 0;
        fw->iq_max_q15 = 32767;
        if(fw->integrator < 0) {
            fw->integrator += fw->recovery_step;
            if(fw->integrator > 0) fw->integrator = 0;
        }
        fw->id_fw_q15 = fw->integrator;  /* p_term=0 при восстановлении */
        return;
    }

    fw->active = 1;
    /* err < 0: нужно увеличить ослабление поля.
     * Пропорционально степени насыщения + интегратор для устранения ошибки. */
    int32_t err = limit_scale_q15 - 32767;  /* отрицательное при насыщении */
    fw->integrator += (fw->ki * err) >> 15;
    fw->integrator = CLAMP(fw->integrator, fw->out_min, fw->out_max);
    fw->id_fw_q15 = ((fw->kp * err) >> 15) + fw->integrator;
    fw->id_fw_q15 = CLAMP(fw->id_fw_q15, fw->out_min, fw->out_max);

    /* Iq limit: available voltage for torque = sqrt(Vmax^2 - Vd^2),
     * где Vd = |vd_q15| — фактическое напряжение Vd прошлого цикла
     * (уже включает вклад FW через PI). Не добавляем id_fw — это ток,
     * не напряжение; сложение разных размерностей давало vd_total > Vmax
     * и iq_max = 0, что останавливало мотор. */
    int32_t vmax = fw->v_max_q15;
    if (vmax <= 0) {
        fw->iq_max_q15 = 0;
        return;
    }
    /* FW-06: |vd| в int64_t — нет UB при INT32_MIN; vmax клэмпнут в Q15. */
    int64_t vd_abs = (vd_q15 >= 0) ? (int64_t)vd_q15 : -(int64_t)vd_q15;
    if (vmax > 32767) vmax = 32767;
    int32_t vmax2 = (int32_t)(((int64_t)vmax * vmax) >> 15);
    int32_t vd2 = (int32_t)((vd_abs * vd_abs) >> 15);
    if (vd2 >= vmax2) {
        fw->iq_max_q15 = 0;
    } else {
        /* sqrt_q15 inline (CORDIC_Sqrt expects q1.31) */
        int32_t diff = vmax2 - vd2;
        if (diff > 32767) diff = 32767;
        int32_t x_q31 = (int32_t)((int64_t)diff * 65536);
        int32_t sqrt_q31 = CORDIC_Sqrt(x_q31);
        fw->iq_max_q15 = (int32_t)(sqrt_q31 >> 16);
    }
}

int32_t FW_GetIdAdd(FluxWeakening *fw) { return fw->id_fw_q15; }
int32_t FW_GetIqLimit(FluxWeakening *fw) { return fw->iq_max_q15; }
int FW_IsActive(FluxWeakening *fw) { return fw->active; }
