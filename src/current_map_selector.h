#ifndef CURRENT_MAP_SELECTOR_H
#define CURRENT_MAP_SELECTOR_H

#include <stdbool.h>
#include <stdint.h>

#include "current_reconstruct.h"
#include "pwm.h"

#define OEW_CURRENT_MAP_MAGIC        0x4F45574Du
#define OEW_CURRENT_MAP_REVISION     3u
#define OEW_CURRENT_MAP_WINDOW_COUNT CURRENT_RECON_MAX_WINDOWS
#define OEW_CURRENT_MAP_SECTOR_COUNT CURRENT_RECON_MAX_SECTORS

typedef struct {
    int16_t mu_min;
    int16_t mu_max;
    int16_t mv_min;
    int16_t mv_max;
    int16_t mw_min;
    int16_t mw_max;
    uint16_t min_margin_ticks;
    int16_t geometry_mod_min_q15;
    int16_t geometry_mod_max_q15;
    uint8_t valid;
    uint8_t reserved; /* 0=statistical legacy bounds, 1=geometric bounds */
} OewPwmRegion;

typedef struct {
    uint16_t board_revision;
    uint32_t pwm_frequency_hz;
    uint32_t timer_arr;
    uint32_t adc_trigger_id;
    uint16_t trigger_offset_ticks;
    uint16_t deadtime_ticks;
    uint32_t adc_clock_hz;
    uint16_t adc_sample_cycles_x2;
    uint8_t adc_resolution;
    uint32_t adc_config_signature;
    uint32_t current_calibration_signature;
} OewMapIdentity;

typedef struct {
    uint32_t characterization_id;
    uint32_t dataset_crc32;
    uint32_t tool_build_id;
    uint32_t qualification_revision;
    uint32_t solver_revision;
    uint32_t certifier_revision;
} OewMapProvenance;

typedef struct {
    uint32_t magic;
    uint16_t revision;
    uint16_t board_revision;
    uint32_t pwm_frequency_hz;
    uint32_t timer_arr;
    uint32_t adc_trigger_id;
    uint16_t trigger_offset_ticks;
    uint16_t deadtime_ticks;
    uint32_t adc_clock_hz;
    uint16_t adc_sample_cycles_x2;
    uint8_t adc_resolution;
    uint32_t adc_config_signature;
    uint32_t current_calibration_signature;
    OewMapProvenance provenance;
    uint8_t startup_sector;
    uint8_t startup_window;
    uint16_t startup_hold_cycles;
    int16_t startup_mu;
    int16_t startup_mv;
    int16_t startup_mw;
    CurrentReconEntry recon[OEW_CURRENT_MAP_SECTOR_COUNT]
                           [OEW_CURRENT_MAP_WINDOW_COUNT];
    OewPwmRegion region[OEW_CURRENT_MAP_SECTOR_COUNT]
                       [OEW_CURRENT_MAP_WINDOW_COUNT];
    uint32_t crc32;
} OewCurrentMap;

uint32_t CurrentMap_CalculateCrc32(const OewCurrentMap *map);
bool CurrentMap_LoadMeasured(const OewCurrentMap *map,
                             const OewMapIdentity *active_identity);
void CurrentMap_Reset(void);
bool CurrentMap_IsReady(void);
uint16_t CurrentMap_GetStartupHoldCycles(void);
bool CurrentMap_SelectInitialStartupContext(PwmSampleContext *context,
                                            int16_t *mu, int16_t *mv, int16_t *mw);
bool CurrentMap_SelectNextContext(int16_t mu, int16_t mv, int16_t mw,
                                  PwmSampleContext *context);

#endif /* CURRENT_MAP_SELECTOR_H */
