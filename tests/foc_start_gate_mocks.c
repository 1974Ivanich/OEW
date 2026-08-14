#include <stdbool.h>
#include <stdint.h>

#include "adc.h"
#include "autotune.h"
#include "encoder.h"
#include "flux_weakening.h"
#include "observer.h"
#include "pll.h"
#include "protect.h"
#include "uart.h"
#include "vf_control.h"
#include "vf_start.h"
#include "voltage_manager.h"

MotorParams g_motor_params;
volatile uint8_t g_clock_fail;

bool test_adc_armed;
bool test_adc_admission;
bool test_adc_offsets_valid = true;
int test_adc_start_rc;
int test_adc_start_count;
int test_adc_stop_count;
int test_adc_calibrate_count;
int test_protect_fault;
uint8_t test_expected_sector;
uint8_t test_expected_window;
bool test_expected_window_valid;

void FocStartGateMock_Reset(void)
{
    test_adc_armed = false;
    test_adc_admission = false;
    test_adc_offsets_valid = true;
    test_adc_start_rc = 0;
    test_adc_start_count = 0;
    test_adc_stop_count = 0;
    test_adc_calibrate_count = 0;
    test_protect_fault = 0;
    test_expected_sector = 0u;
    test_expected_window = 0u;
    test_expected_window_valid = false;
    g_clock_fail = 0u;
}

int ADC_CalibrateOffsets(void)
{
    ++test_adc_calibrate_count;
    return 0;
}

bool ADC_OffsetsAreValid(void) { return test_adc_offsets_valid; }
int32_t ADC_GetVbus_mV(void) { return 150000; }
int32_t ADC_GetI1_mA(void) { return 0; }
int32_t ADC_GetI2_mA(void) { return 0; }
int32_t ADC_GetIres_mA(void) { return 0; }
bool ADC_FrameIsControlValid(const AdcFrame *frame)
{
    return frame != 0 && frame->status == ADC_FRAME_VALID;
}
int ADC_InjectedStart(void)
{
    ++test_adc_start_count;
    if (test_adc_start_rc == 0) test_adc_armed = true;
    return test_adc_start_rc;
}
void ADC_InjectedStop(void)
{
    test_adc_armed = false;
    ++test_adc_stop_count;
}
bool ADC_InjectedIsArmed(void) { return test_adc_armed; }
int ADC_StartConversion(void) { return 0; }
void ADC_SetControlAdmission(bool admitted) { test_adc_admission = admitted; }
void ADC_SetExpectedWindow(uint8_t sector, uint8_t window, bool valid)
{
    test_expected_sector = sector;
    test_expected_window = window;
    test_expected_window_valid = valid;
}

int32_t ENC_GetSpeed_rpm(void) { return 0; }

int PROTECT_IsFault(void) { return test_protect_fault; }
void UART_SendStr(const char *s) { (void)s; }
int UART_TrySendStr(const char *s) { (void)s; return 1; }

int VFC_IsRunning(void) { return 0; }
void VFC_Stop(void) { }

int32_t BEMF_GetMagnitude(BEMFObserver *obs) { (void)obs; return 0; }
void BEMF_Init(BEMFObserver *obs, int32_t r, int32_t l, int32_t ts, int32_t vdc)
{
    (void)obs; (void)r; (void)l; (void)ts; (void)vdc;
}
void BEMF_Update(BEMFObserver *obs, int32_t va, int32_t vb, int32_t ia, int32_t ib)
{
    (void)obs; (void)va; (void)vb; (void)ia; (void)ib;
}
uint8_t BEMF_IsValid(const BEMFObserver *obs) { (void)obs; return 0u; }

int32_t PLL_GetSpeed(PLL *pll) { (void)pll; return 0; }
void PLL_Init(PLL *pll, int32_t kp, int32_t ki) { (void)pll; (void)kp; (void)ki; }
void PLL_Update(PLL *pll, int32_t ea, int32_t eb) { (void)pll; (void)ea; (void)eb; }

void FW_Init(FluxWeakening *fw, int32_t kp, int32_t ki) { (void)fw; (void)kp; (void)ki; }
void FW_SetBaseSpeedRpm(FluxWeakening *fw, int32_t rpm) { (void)fw; (void)rpm; }
void FW_SetVmaxQ15(FluxWeakening *fw, int32_t v) { (void)fw; (void)v; }
int32_t FW_GetIdAdd(FluxWeakening *fw) { (void)fw; return 0; }
void FW_Update(FluxWeakening *fw, int32_t vd, int32_t vq, int32_t ls, int32_t idb, int32_t spd)
{
    (void)fw; (void)vd; (void)vq; (void)ls; (void)idb; (void)spd;
}

void VM_Init(VoltageManager *vm, int32_t vmax, VMPriority priority)
{
    (void)vm; (void)vmax; (void)priority;
}
void VM_SetVmax(VoltageManager *vm, int32_t v) { (void)vm; (void)v; }
int32_t VM_GetVmax(const VoltageManager *vm) { (void)vm; return 29490; }
int32_t VM_GetLimitScale(const VoltageManager *vm) { (void)vm; return 32767; }
void VM_Update(VoltageManager *vm, int32_t vd, int32_t vq) { (void)vm; (void)vd; (void)vq; }

void VF_Init(VFStart *vf, int32_t target, int32_t ramp) { (void)vf; (void)target; (void)ramp; }
void VF_SetTarget(VFStart *vf, int32_t target) { (void)vf; (void)target; }
void VF_Update(VFStart *vf) { (void)vf; }
int VF_IsComplete(VFStart *vf) { (void)vf; return 0; }
int32_t VF_GetSpeed(VFStart *vf) { (void)vf; return 0; }
int32_t VF_GetTheta(VFStart *vf) { (void)vf; return 0; }

void TRIG_High(void) { }
void TRIG_Low(void) { }
