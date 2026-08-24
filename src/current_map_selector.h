#ifndef CURRENT_MAP_SELECTOR_H
#define CURRENT_MAP_SELECTOR_H

#include <stdbool.h>
#include <stdint.h>

#include "current_reconstruct.h"
#include "pwm.h"

#define OEW_CURRENT_MAP_MAGIC       0x4F45574Du /* "OEWM" */
#define OEW_CURRENT_MAP_REVISION    2u
#define OEW_CURRENT_MAP_WINDOW_COUNT CURRENT_RECON_MAX_WINDOWS
#define OEW_CURRENT_MAP_SECTOR_COUNT CURRENT_RECON_MAX_SECTORS

/* An admissible modulation region for exactly one measured sector/window row.
 * Bounds are Q15 inclusive and must be inside the linear modulation range.
 * Every next FOC command must match exactly one row; overlap is rejected. */
typedef struct {
    int16_t mu_min;
    int16_t mu_max;
    int16_t mv_min;
    int16_t mv_max;
    int16_t mw_min;
    int16_t mw_max;

    uint16_t min_margin_ticks; /* measured aperture margin including blanking */
    uint8_t valid;             /* scope/calibration evidence accepted */
    uint8_t reserved;
} OewPwmRegion;

/* Runtime/physical configuration identity. Configuration signatures are
 * canonical CRCs of the active ADC and current-measurement configuration. */
typedef struct {
    uint16_t board_revision;
    uint32_t pwm_frequency_hz;
    uint32_t timer_arr;
    uint32_t adc_trigger_id;
    uint16_t trigger_offset_ticks;
    uint16_t deadtime_ticks;
    uint32_t adc_config_signature;
    uint32_t current_calibration_signature;
} OewMapIdentity;

/* Provenance of the offline characterization artifact. These fields are
 * included in the map CRC and allow the deployed map to be traced back to the
 * measured dataset and the exact host characterization implementation. */
typedef struct {
    uint32_t characterization_id;
    uint32_t dataset_crc32;
    uint32_t tool_build_id;
    uint32_t qualification_revision;
    uint32_t solver_revision;
    uint32_t certifier_revision;
} OewMapProvenance;

/* Persistent engineering record produced offline by injected-channel
 * characterization. Before CRC generation all padding/reserved bytes must be
 * zero. `recon` uses the same sector/window numbering as AdcFrame. */
typedef struct {
    uint32_t magic;
    uint16_t revision;
    uint16_t board_revision;
    uint32_t pwm_frequency_hz;
    uint32_t timer_arr;
    uint32_t adc_trigger_id;
    uint16_t trigger_offset_ticks;
    uint16_t deadtime_ticks;
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

    uint32_t crc32; /* CRC-32/ISO-HDLC with this field treated as zero. */
} OewCurrentMap;

/* Board identity from compiled configuration. Loading is rejected if any
 * physical/configuration identity field differs from the active build. */
typedef OewMapIdentity OewMapIdentity;

uint32_t CurrentMap_CalculateCrc32(const OewCurrentMap *map);

/* Validates metadata, CRC, provenance, all reconstruction entries and all PWM
 * regions; then atomically installs the reconstruction map and selector map.
 * Must be called only with PWM disabled, ADC injected stopped and FOC not
 * running. */
bool CurrentMap_LoadMeasured(const OewCurrentMap *map,
                             const OewMapIdentity *active_identity);
void CurrentMap_Reset(void);
bool CurrentMap_IsReady(void);
uint16_t CurrentMap_GetStartupHoldCycles(void);

/* Chooses the measured low-energy initial state. It never derives a context
 * from a nominal 0-vector and never emits a default (0,0,true). */
bool CurrentMap_SelectInitialStartupContext(PwmSampleContext *context,
                                            int16_t *mu, int16_t *mv, int16_t *mw);

/* Chooses the sole validated sector/window region covering the next FOC vector.
 * Returns false for clipping, coverage holes, overlap or invalid map state. */
bool CurrentMap_SelectNextContext(int16_t mu, int16_t mv, int16_t mw,
                                  PwmSampleContext *context);

#endif /* CURRENT_MAP_SELECTOR_H */
