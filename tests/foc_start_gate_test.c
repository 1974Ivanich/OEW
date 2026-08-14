#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "current_map_selector.h"
#include "foc.h"
#include "pwm.h"
#include "stm32g474xx.h"

TIM_TypeDef host_tim1;
TIM_TypeDef host_tim8;
GPIO_TypeDef host_gpioa;
GPIO_TypeDef host_gpiob;
GPIO_TypeDef host_gpioc;
GPIO_TypeDef host_gpiod;
RCC_TypeDef host_rcc;
uint32_t SystemCoreClock = 170000000u;

extern bool test_adc_armed;
extern bool test_adc_admission;
extern int test_adc_start_count;
extern int test_adc_stop_count;
extern void FocStartGateMock_Reset(void);

static void host_reset_registers(void)
{
    memset(&host_tim1, 0, sizeof(host_tim1));
    memset(&host_tim8, 0, sizeof(host_tim8));
    memset(&host_gpioa, 0, sizeof(host_gpioa));
    memset(&host_gpiob, 0, sizeof(host_gpiob));
    memset(&host_gpioc, 0, sizeof(host_gpioc));
    memset(&host_gpiod, 0, sizeof(host_gpiod));
    memset(&host_rcc, 0, sizeof(host_rcc));
}

static void host_set_interlock(bool safety_ok, bool tim1_bkin_high, bool tim8_bkin_high)
{
    const uint32_t b_inputs = (safety_ok ? (1u << 11) : 0u) |
                              (tim1_bkin_high ? (1u << 12) : 0u);
    host_gpiob.IDR = (host_gpiob.IDR & ~((1u << 11) | (1u << 12))) | b_inputs;
    if (tim8_bkin_high) {
        host_gpiod.IDR |= (1u << 2);
    } else {
        host_gpiod.IDR &= ~(1u << 2);
    }
}

static void build_valid_map(OewCurrentMap *map, OewMapIdentity *identity)
{
    uint8_t sector;
    uint8_t window;

    memset(map, 0, sizeof(*map));
    map->magic = OEW_CURRENT_MAP_MAGIC;
    map->revision = OEW_CURRENT_MAP_REVISION;
    map->board_revision = 7u;
    map->pwm_frequency_hz = 5000u;
    map->timer_arr = 999u;
    map->adc_trigger_id = 0x01020304u;
    map->startup_sector = 0u;
    map->startup_window = 0u;
    map->startup_hold_cycles = 4u;
    map->startup_mu = -30000;
    map->startup_mv = 0;
    map->startup_mw = 0;

    for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
        for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
            const uint8_t slot = (uint8_t)(sector * OEW_CURRENT_MAP_WINDOW_COUNT + window);
            const int16_t min = (int16_t)(-32768 + (int16_t)slot * 5000);
            OewPwmRegion *region = &map->region[sector][window];
            CurrentReconEntry *recon = &map->recon[sector][window];

            region->mu_min = min;
            region->mu_max = (int16_t)(min + 4000);
            region->mv_min = -32768;
            region->mv_max = 32767;
            region->mw_min = -32768;
            region->mw_max = 32767;
            region->min_margin_ticks = 10u;
            region->valid = 1u;

            recon->valid = true;
            recon->phase_a = 0u;
            recon->phase_b = 1u;
            recon->m00 = CURRENT_RECON_COEFF_SCALE;
            recon->m01 = 0;
            recon->m10 = 0;
            recon->m11 = CURRENT_RECON_COEFF_SCALE;
        }
    }

    map->crc32 = CurrentMap_CalculateCrc32(map);
    identity->board_revision = map->board_revision;
    identity->pwm_frequency_hz = map->pwm_frequency_hz;
    identity->timer_arr = map->timer_arr;
    identity->adc_trigger_id = map->adc_trigger_id;
}

static void assert_power_path_off(void)
{
    assert(!FOC_IsRunning());
    assert(!test_adc_armed);
    assert((host_tim1.CR1 & TIM_CR1_CEN) == 0u);
    assert((host_tim8.CR1 & TIM_CR1_CEN) == 0u);
    assert((host_tim1.BDTR & TIM_BDTR_MOE) == 0u);
    assert((host_tim8.BDTR & TIM_BDTR_MOE) == 0u);
    assert((host_gpiob.ODR & ((1u << 4) | (1u << 5))) == 0u);
}

int main(void)
{
    OewCurrentMap map;
    OewMapIdentity identity;

    host_reset_registers();
    FocStartGateMock_Reset();
    CurrentMap_Reset();
    PWM_Init();

    /* Gate 1: a missing measured map must reject before touching ADC or PWM. */
    assert(FOC_Start() == FOC_START_MAP_UNVERIFIED);
    assert(test_adc_start_count == 0);
    assert(test_adc_stop_count == 0);
    assert(!test_adc_admission);
    assert_power_path_off();

    /* Gate 2: even a genuine map cannot defeat physical default-deny. FOC arms
     * injected ADC before PWM_Enable by design; failed enable must unwind it. */
    build_valid_map(&map, &identity);
    assert(CurrentMap_LoadMeasured(&map, &identity));
    assert(CurrentMap_IsReady());
    assert(!PWM_HardwareInterlockHealthy());
    assert(FOC_Start() == FOC_START_PWM_ENABLE_FAILED);
    assert(test_adc_start_count == 1);
    assert(test_adc_stop_count == 1);
    assert(!test_adc_admission);
    assert_power_path_off();

    /* Success path: the same real measured map plus a healthy physical input
     * must produce the complete admission → ADC-arm → PWM-enable state. */
    FocStartGateMock_Reset();
    PWM_Init();
    host_set_interlock(true, true, true);
    assert(PWM_HardwareInterlockHealthy());
    assert(FOC_Start() == FOC_START_OK);
    assert(FOC_IsRunning());
    assert(test_adc_admission);
    assert(test_adc_armed);
    assert(test_adc_start_count == 1);
    assert((host_tim1.CR1 & TIM_CR1_CEN) != 0u);
    assert((host_tim8.CR1 & TIM_CR1_CEN) != 0u);
    assert((host_tim1.BDTR & TIM_BDTR_MOE) != 0u);
    assert((host_tim8.BDTR & TIM_BDTR_MOE) != 0u);
    assert((host_gpiob.ODR & ((1u << 4) | (1u << 5))) == ((1u << 4) | (1u << 5)));

    FOC_Stop();
    assert_power_path_off();
    CurrentMap_Reset();

    puts("foc_start_gate_test: PASS");
    return 0;
}
