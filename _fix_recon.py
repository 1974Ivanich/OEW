# -*- coding: utf-8 -*-
"""Интеграция current_reconstruct (двухшунтовая реконструкция) + P0/P1/P2
из ревизии production: FOC_RunFrame, FOC_Start->int с гейтами, init-fail,
build hygiene autotune.c, Werror=misleading-indentation."""

import io, os, shutil

SRC = r'C:\ST\boyler\Motor'
ZIP = r'C:/Users/190/AppData/Local/Temp/rec/generated_current_reconstruct'

def edit(path, pairs):
    with io.open(path, 'r', encoding='utf-8', newline='') as f:
        text = f.read()
    eol = '\r\n' if '\r\n' in text else '\n'
    for old, new in pairs:
        old = old.replace('\n', eol); new = new.replace('\n', eol)
        n = text.count(old)
        print(('OK ' if n == 1 else 'FAIL(%d)' % n) + ' : ' + old.splitlines()[0][:60])
        if n != 1: raise SystemExit(1)
        text = text.replace(old, new)
    with io.open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text)

# ── 1. Копирование файлов модуля и теста ────────────────────────────────
for src, dst in [
    (os.path.join(ZIP, 'src/current_reconstruct.c'), os.path.join(SRC, 'src/current_reconstruct.c')),
    (os.path.join(ZIP, 'src/current_reconstruct.h'), os.path.join(SRC, 'src/current_reconstruct.h')),
    (os.path.join(ZIP, 'tests/current_reconstruct_test.c'), os.path.join(SRC, 'tests/current_reconstruct_test.c')),
    (os.path.join(ZIP, 'tests/adc_frame_stub.c'), os.path.join(SRC, 'tests/adc_frame_stub.c')),
]:
    shutil.copyfile(src, dst)
    print('COPY:', dst)

# ── 2. foc.h: FOC_RunFrame + int FOC_Start + коды ───────────────────────
edit(os.path.join(SRC, 'src/foc.h'), [
    ("""void FOC_Run(void);
void FOC_Start(void);""",
     """/* Ревью «два DC-link shunt + CT»: FOC_Run принимает свежий AdcFrame;
 * Current_Reconstruct() отдаёт фазные токи ТОЛЬКО из доказанных строк карты.
 * Legacy no-argument FOC_Run больше НЕ существует — вызовы из control-пути
 * (ADC1_2_IRQHandler) обязаны передавать фрейм. */
void FOC_RunFrame(const AdcFrame *frame);

/* FOC_Start возвращает код результата: PWM/EN включаются ТОЛЬКО при
 * загруженной карте реконструкции (иначе fail-closed). */
#define FOC_START_OK                  0
#define FOC_START_CLOCK_OR_FAULT     -1
#define FOC_START_MAP_UNVERIFIED     -2
#define FOC_START_CALIBRATION_FAILED -3
#define FOC_START_ADC_ARM_FAILED     -4
int FOC_Start(void);"""),
])

# ── 3. foc.c ─────────────────────────────────────────────────────────────
foc_c = os.path.join(SRC, 'src/foc.c')
edit(foc_c, [
    ("""#include "protect.h"    /* PROTECT_IsFault — interlock FOC_Start (ревью PR-02) */""",
     """#include "protect.h"    /* PROTECT_IsFault — interlock FOC_Start (ревью PR-02) */
#include "current_reconstruct.h"   /* двухшунтовая реконструкция фазных токов */
extern volatile uint8_t g_clock_fail;   /* main.c: PLL-гвард — силовая часть запрещена */"""),
    # FOC_Start: int + гейты
    ("""void FOC_Start(void) {
    if(foc_running) return;
    if(VFC_IsRunning()) return;  /* не запускать поверх V/f-режима */
    /* Ревью PR-02: latched fault — interlock: PWM не включается поверх
     * аварии; сброс только через PROTECT_RequestClear() (команда 'f'). */
    if(PROTECT_IsFault()) {
        UART_SendStr("FOC start blocked: fault latched, send 'f' to clear\\r\\n");
        return;
    }
    if(!foc_initialized) FOC_Init();
    /* Калибровка нуля токов — непосредственно перед запуском,
     * пока инвертор выключен (токи истинно нулевые). */
    ADC_CalibrateOffsets();""",
     """int FOC_Start(void) {
    if(foc_running) return FOC_START_OK;
    if(VFC_IsRunning()) return FOC_START_CLOCK_OR_FAULT;  /* не запускать поверх V/f-режима */
    /* Ревью PR-02: latched fault — interlock: PWM не включается поверх
     * аварии; сброс только через PROTECT_RequestClear() (команда 'f'). */
    if(g_clock_fail || PROTECT_IsFault()) {
        UART_SendStr("FOC start blocked: clock fail or fault latched, send 'f' to clear\\r\\n");
        return FOC_START_CLOCK_OR_FAULT;
    }
    /* Ревью P0 (два DC-link shunt): карта реконструкции должна быть
     * загружена и доказана ДО первого TRGO/PWM. Пустая карта = отказ,
     * PWM/EN остаются выключенными (fail-closed). */
    if(!CurrentRecon_IsReady()) {
        UART_SendStr("FOC start blocked: current map unverified (run ch/chu/chv/chw first)\\r\\n");
        return FOC_START_MAP_UNVERIFIED;
    }
    if(!foc_initialized) FOC_Init();
    /* Калибровка нуля токов — непосредственно перед запуском,
     * пока инвертор выключен (токи истинно нулевые). */
    if(!ADC_OffsetsAreValid() && ADC_CalibrateOffsets() != 0) {
        UART_SendStr("FOC start blocked: offset calibration failed\\r\\n");
        return FOC_START_CALIBRATION_FAILED;
    }"""),
    # конец FOC_Start: admission + rc
    ("""    vbus_filtered_mv = ADC_GetVbus_mV();
    if(vbus_filtered_mv < 1000) vbus_filtered_mv = 1000;
    ADC_InjectedStart();           /* ADC ждёт TIM1_TRGO */
    PWM_Enable();                  /* CEN → TRGO → ADC → ISR → FOC_Run */
}""",
     """    vbus_filtered_mv = ADC_GetVbus_mV();
    if(vbus_filtered_mv < 1000) vbus_filtered_mv = 1000;
    /* Разрешаем control-valid фреймы ТОЛЬКО после того, как все
     * предусловия прошли (карта загружена, калибровка валидна). */
    ADC_SetControlAdmission(true);
    ADC_SetExpectedWindow(0u, 0u, true);
    if(ADC_InjectedStart() != 0) { /* ADC ждёт TIM1_TRGO */
        ADC_SetControlAdmission(false);
        return FOC_START_ADC_ARM_FAILED;
    }
    PWM_Enable();                  /* CEN → TRGO → ADC → ISR → FOC_RunFrame */
    return FOC_START_OK;
}"""),
    # FOC_Run → FOC_RunFrame: реконструкция вместо ADC_GetI*
    ("""void FOC_Run(void) {
    if(!foc_running) return;

    /* 1. Чтение токов АЦП (данные из injected group JDR1-4, обновлены в ADC ISR).
     * Ires — трансформаторный датчик суммы токов A+B+C (PA6 = ADC2_IN3),
     * теперь с правильным масштабом 100 мВ/А.
     * В OEW сумма фазных токов не равна нулю, поэтому восстанавливаем
     * третий ток: iw = Ires − iu − iv, и применяем полное 3-датчиковое
     * преобразование Кларка. */
    int32_t i1_ma = ADC_GetI1_mA();
    int32_t i2_ma = ADC_GetI2_mA();
    int32_t ires_ma = ADC_GetIres_mA();
    int32_t iw_ma = ires_ma - i1_ma - i2_ma;

    /* Приведение к внутреннему масштабу (Q15) — делим на 100.
     * Полный диапазон ±26А → ±26000 мА → ±260 в Q15. */
    int32_t iu = i1_ma / 100;
    int32_t iv = i2_ma / 100;
    /* iw = Ires − Iu − Iv (реальный третий фазный ток). Используется
     * одновременно в 3-датчиковом Clarke и в dead-time компенсации. */
    int32_t iw = iw_ma / 100;

    /* 1b. Фильтр Vbus: IIR 1-го порядка, 1/16 нового значения.
     * Все алгоритмы ниже получают отфильтрованное Vbus. */
    {
        int32_t vbus_raw = ADC_GetVbus_mV();
        if(vbus_raw < 1000) vbus_raw = 1000;
        if(vbus_filtered_mv == 0) vbus_filtered_mv = vbus_raw;
        vbus_filtered_mv = (vbus_filtered_mv * 15 + vbus_raw) / 16;
        if(vbus_filtered_mv < 1000) vbus_filtered_mv = 1000;
    }""",
     """void FOC_RunFrame(const AdcFrame *frame) {
    if(!foc_running) return;
    if(frame == 0) { FOC_Stop(); return; }

    /* 1. Реконструкция фазных токов из двух DC-link шунтов (двухшунтовая
     * топология, ревью «два DC-link shunt + CT»): idc1/idc2 в одной апертуре
     * → две независимые фазы по строке карты, третья из KCL. CT — только
     * диагностика (phase.ict_ma). Невалидный фрейм/строка карты = стоп,
     * ни один PI-цикл на непроверенных данных не выполняется. */
    PhaseCurrents phase;
    if(!Current_Reconstruct(frame, &phase)) {
        FOC_Stop();
        return;
    }

    /* Приведение к внутреннему масштабу (Q15) — делим на 100.
     * Полный диапазон ±26А → ±26000 мА → ±260 в Q15. */
    int32_t iu = phase.iu_ma / 100;
    int32_t iv = phase.iv_ma / 100;
    int32_t iw = phase.iw_ma / 100;

    /* 1b. Фильтр Vbus: IIR 1-го порядка, 1/16 нового значения, из СВЕЖЕГО
     * фрейма (не из геттера — геттер мог прочитать уже следующий фрейм).
     * Все алгоритмы ниже получают отфильтрованное Vbus. */
    {
        int32_t vbus_raw = frame->vbus_mv;
        if(vbus_raw < 1000) vbus_raw = 1000;
        if(vbus_filtered_mv == 0) vbus_filtered_mv = vbus_raw;
        vbus_filtered_mv = (vbus_filtered_mv * 15 + vbus_raw) / 16;
        if(vbus_filtered_mv < 1000) vbus_filtered_mv = 1000;
    }"""),
])

# ── 4. main.c: ISR, команда '1', init-fail ──────────────────────────────
edit(os.path.join(SRC, 'main.c'), [
    ("""    if(FOC_IsRunning() && (TIM1->CR1 & TIM_CR1_CEN)) {
        PROTECT_Check();
        if(PROTECT_IsFault()) FOC_Stop();
        else FOC_Run();
    }""",
     """    if(FOC_IsRunning() && (TIM1->CR1 & TIM_CR1_CEN)) {
        PROTECT_Check();
        if(PROTECT_IsFault()) FOC_Stop();
        else FOC_RunFrame(&frame);
    }"""),
    ("""                else { VFC_Stop(); FOC_Start(); DBG_STR("FOC started\\r\\n> "); }""",
     """                else {
                    VFC_Stop();
                    int rc = FOC_Start();
                    if(rc == FOC_START_OK) DBG_STR("FOC started\\r\\n> ");
                    else UART_SendTelemetry("@FOC:START:FAIL:rc=%d (0=OK -1=clock/fault -2=map_unverified -3=calib -4=arm)\\r\\n> ", rc);
                }"""),
    ("""    ADC_Init(); UART_SendStr("ADC OK\\r\\n");
    PWM_Init(); UART_SendStr("PWM OK\\r\\n");
    CORDIC_Init(); UART_SendStr("CORDIC OK\\r\\n");
    PROTECT_Init(); UART_SendStr("PROTECT OK\\r\\n");
    FOC_Init(); UART_SendStr("FOC init OK\\r\\n");
    ADC_InjectedInit(); UART_SendStr("ADC injected OK\\r\\n");""",
     """    /* Ревью P1: ошибки инициализации ADC — latched (g_clock_fail), PWM_Enable
     * впоследствии запрещён; не печатать success вслепую. */
    if(ADC_Init() != 0 || ADC_InjectedInit() != 0) {
        g_clock_fail = 1;
        UART_SendStr("ADC INIT FAIL: power stage locked\\r\\n");
    } else {
        UART_SendStr("ADC OK, injected OK\\r\\n");
    }
    PWM_Init(); UART_SendStr("PWM OK\\r\\n");
    CORDIC_Init(); UART_SendStr("CORDIC OK\\r\\n");
    PROTECT_Init(); UART_SendStr("PROTECT OK\\r\\n");
    FOC_Init(); UART_SendStr("FOC init OK\\r\\n");"""),
])

# ── 5. autotune.c: build hygiene ────────────────────────────────────────
at = os.path.join(SRC, 'src/autotune.c')
pairs = []
# 5a. мёртвые помощники tim1/tim8_*
pairs.append((
"""static void tim1_enable(void) {
    TIM1->CCER |= TIM_CCER_CC1E | TIM_CCER_CC1NE
               |  TIM_CCER_CC2E | TIM_CCER_CC2NE
               |  TIM_CCER_CC3E | TIM_CCER_CC3NE;
    TIM1->BDTR |= TIM_BDTR_MOE;
    TIM1->CR1  |= TIM_CR1_CEN;
    GPIOB->BSRR = (1U<<4);  /* EN1 = HIGH — ПОСЛЕДНИМ (AT-2S-02) */
}

static void tim1_disable(void) {
    GPIOB->BSRR = (1U<<(16+4));  /* EN1 = LOW — ПЕРВЫМ (AT-2S-02) */
    TIM1->CR1  &= ~TIM_CR1_CEN;
    TIM1->BDTR &= ~TIM_BDTR_MOE;
    TIM1->CCER &= ~(TIM_CCER_CC1E | TIM_CCER_CC1NE
                  | TIM_CCER_CC2E | TIM_CCER_CC2NE
                  | TIM_CCER_CC3E | TIM_CCER_CC3NE);
}

static void tim8_enable(void);
static void tim8_disable(void);
static void both_enable(void);
static void both_disable(void);
static int8_t AT_SafetyCheck(void);   /* определена ниже — для ch/chu/chv/chw (AT-2S-01) */""",
"""static void both_enable(void);
static void both_disable(void);
static int8_t AT_SafetyCheck(void);   /* определена ниже — для ch/chu/chv/chw (AT-2S-01) */"""))
# 5b. определения tim8_enable/tim8_disable
pairs.append((
"""static void tim8_enable(void) {
    /* Безопасная последовательность: сначала конфигурируем таймер,
     * затем включаем силовой драйвер, затем запускаем таймер. */
    TIM8->CCER |= TIM_CCER_CC1E | TIM_CCER_CC1NE | TIM_CCER_CC2E | TIM_CCER_CC2NE | TIM_CCER_CC3E | TIM_CCER_CC3NE;
    TIM8->BDTR |= TIM_BDTR_MOE;
    TIM8->EGR |= TIM_EGR_UG; TIM8->EGR &= ~TIM_EGR_UG;
    TIM8->CR1  |= TIM_CR1_CEN;
    GPIOB->BSRR = (1U<<5);  /* EN2 = HIGH — ПОСЛЕДНИМ (AT-2S-02) */
}

static void tim8_disable(void) {
    GPIOB->BSRR = (1U<<(16+5));  /* EN2 = LOW — ПЕРВЫМ (AT-2S-02) */
    TIM8->CR1  &= ~TIM_CR1_CEN;
    TIM8->BDTR &= ~TIM_BDTR_MOE;
    TIM8->CCER &= ~(TIM_CCER_CC1E | TIM_CCER_CC1NE | TIM_CCER_CC2E | TIM_CCER_CC2NE | TIM_CCER_CC3E | TIM_CCER_CC3NE);
}

""",
""""""))
# 5c. unused period
pairs.append((
"""    uint16_t arr    = PWM_GetARR();
    uint32_t period = (uint32_t)arr + 1U;
    int32_t  vbus   = ADC_GetVbus_mV();""",
"""    uint16_t arr    = PWM_GetARR();
    (void)arr;
    int32_t  vbus   = ADC_GetVbus_mV();"""))
# 5d. misleading-indentation: разнести клампы
for v in ('da', 'db', 'dc'):
    pairs.append((
"        if (%s < 0) %s = 0; if (%s > AT_RR_DUTY_MAX) %s = AT_RR_DUTY_MAX;" % (v, v, v, v),
"        if (%s < 0) { %s = 0; }\n        if (%s > AT_RR_DUTY_MAX) { %s = AT_RR_DUTY_MAX; }" % (v, v, v, v)))
edit(at, pairs)

# ── 6. Makefile ─────────────────────────────────────────────────────────
mk = os.path.join(SRC, 'Makefile')
with io.open(mk, 'r', encoding='utf-8', newline='') as f:
    text = f.read()
eol = '\r\n' if '\r\n' in text else '\n'
# C_SOURCES: добавить current_reconstruct.c после control_isr.c / foc_handoff_gate.c
old_cs = text[text.index('C_SOURCES'):text.index('C_SOURCES') + 800]
new_cs = old_cs.replace('src/control_isr.c \\', 'src/control_isr.c \\\nsrc/current_reconstruct.c \\')
if new_cs == old_cs:
    print('FAIL: C_SOURCES anchor'); raise SystemExit(1)
text = text.replace(old_cs, new_cs)
# CFLAGS: Werror для misleading-indentation и implicit-function-declaration
old_cf = 'CFLAGS += -ffunction-sections -fdata-sections -std=c99'
if old_cf not in text:
    print('FAIL: CFLAGS anchor'); raise SystemExit(1)
text = text.replace(old_cf, old_cf + '\nCFLAGS += -Werror=misleading-indentation -Werror=implicit-function-declaration')
# test target: добавить current_reconstruct_test.exe после adc_frame_host_test.exe
if 'adc_frame_host_test' in text:
    anchor = 'test-hosted:'
    idx = text.find(anchor)
    if idx < 0:
        print('FAIL: test-hosted anchor'); raise SystemExit(1)
    # найдём блок adc_frame_host_test до следующего правила
    blk_start = text.find('tests/adc_frame_host_test.exe:')
    blk_end = text.find('\n\n', blk_start)
    blk = text[blk_start:blk_end]
    new_blk = blk + '\n' + eol.join([
        '',
        'tests/current_reconstruct_test.exe: tests/current_reconstruct_test.c src/current_reconstruct.c src/current_reconstruct.h src/adc.h tests/adc_frame_stub.c',
        '\t$(HOSTED_GCC) -std=c99 -Wall -Wextra -Werror -Isrc src/current_reconstruct.c tests/current_reconstruct_test.c tests/adc_frame_stub.c -o $@',
    ])
    text = text.replace(blk, new_blk)
    # список test-hosted: добавить исполняемый файл
    if 'current_reconstruct_test.exe' not in text:
        print('FAIL: test list update'); raise SystemExit(1)
else:
    print('FAIL: adc_frame_host_test anchor'); raise SystemExit(1)
with io.open(mk, 'w', encoding='utf-8', newline='') as f:
    f.write(text)
print('Makefile: OK')
print('ALL DONE')
