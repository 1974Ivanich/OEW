#ifndef MAP_ARTIFACT_PIPELINE_H
#define MAP_ARTIFACT_PIPELINE_H

#include <stdbool.h>
#include <stdint.h>

#include "map_measurement_accumulator.h"
#include "map_measurement_solver.h"
#include "map_region_certifier.h"
#include "map_artifact_writer.h"

/* Host-side orchestration of the L3 measurement pipeline:
 *
 *   MapMeasurementAccumulator (per row) -> MapMeasurement_SolveM
 *     -> MapRegionCertify -> MapArtifactWriter_Build
 *
 * The pipeline is deliberately NOT part of firmware: it consumes already
 * qualified host-side measurement evidence (samples + modulation grid cells)
 * and produces the CRC-protected OewCurrentMap v2 artifact. Every row must
 * pass all three stages; any failure aborts the whole run (fail-closed), so
 * an unqualified row can never end up in a deployable artifact.
 */

typedef enum {
    MAP_PIPELINE_OK = 0,
    MAP_PIPELINE_BAD_ARGUMENT,
    MAP_PIPELINE_ACCUM_FAILED,
    MAP_PIPELINE_SOLVER_FAILED,
    MAP_PIPELINE_CERT_FAILED,
    MAP_PIPELINE_BUILD_FAILED
} MapPipelineStatus;

typedef struct {
    MapAccumQualification accum;
    MapSolverQualification solver;
    MapRegionQualification region;
} MapPipelineQualifications;

/* Per-row measurement evidence. `samples` feed the accumulator and `cells`
 * feed the region certifier for exactly one sector/window row. */
typedef struct {
    const MapMeasurementSample *samples;
    uint16_t sample_count;
    const MapGridCell *cells;
    uint16_t cell_count;
} MapPipelineRowInput;

typedef struct {
    OewMapIdentity identity;
    OewMapProvenance provenance;
    uint8_t startup_sector;
    uint8_t startup_window;
    uint16_t startup_hold_cycles;
    int16_t startup_mu;
    int16_t startup_mv;
    int16_t startup_mw;
    /* Reference manifest for the whole campaign; the accumulator validates
     * every sample's PWM identity against it. */
    MapReferenceManifest manifest;
    MapPipelineQualifications qualifications;
    MapPipelineRowInput rows[OEW_CURRENT_MAP_SECTOR_COUNT]
                            [OEW_CURRENT_MAP_WINDOW_COUNT];
} MapPipelineInput;

typedef struct {
    uint8_t failed_sector;
    uint8_t failed_window;
    MapAccumStatus accum_status;
    MapSolverStatus solver_status;
    MapCertStatus cert_status;
    uint8_t ready;
} MapPipelineReport;

MapPipelineStatus MapArtifactPipeline_Run(const MapPipelineInput *input,
                                          OewCurrentMap *out,
                                          MapPipelineReport *report);

#endif /* MAP_ARTIFACT_PIPELINE_H */
