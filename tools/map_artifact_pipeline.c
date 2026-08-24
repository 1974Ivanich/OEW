#include "map_artifact_pipeline.h"

#include <string.h>

MapPipelineStatus MapArtifactPipeline_Run(const MapPipelineInput *input,
                                          OewCurrentMap *out,
                                          MapPipelineReport *report)
{
    CurrentReconEntry recon[OEW_CURRENT_MAP_SECTOR_COUNT]
                           [OEW_CURRENT_MAP_WINDOW_COUNT];
    OewPwmRegion region[OEW_CURRENT_MAP_SECTOR_COUNT]
                       [OEW_CURRENT_MAP_WINDOW_COUNT];
    MapPipelineReport r;
    uint8_t sector;
    uint8_t window;

    memset(&r, 0, sizeof(r));
    if (input == 0 || out == 0) {
        if (report != 0) *report = r;
        return MAP_PIPELINE_BAD_ARGUMENT;
    }
    if (input->startup_sector >= OEW_CURRENT_MAP_SECTOR_COUNT ||
        input->startup_window >= OEW_CURRENT_MAP_WINDOW_COUNT ||
        input->startup_hold_cycles == 0u) {
        if (report != 0) *report = r;
        return MAP_PIPELINE_BAD_ARGUMENT;
    }

    memset(recon, 0, sizeof(recon));
    memset(region, 0, sizeof(region));

    for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
        for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
            const MapPipelineRowInput *row = &input->rows[sector][window];
            MapMeasurementAccumulator accumulator;
            MapAccumRowReport accum_report;
            MapSolverReport solver_report;
            MapRegionReport region_report;
            uint16_t i;

            if (row->samples == 0 || row->sample_count == 0u ||
                row->cells == 0 || row->cell_count == 0u) {
                r.failed_sector = sector;
                r.failed_window = window;
                if (report != 0) *report = r;
                return MAP_PIPELINE_BAD_ARGUMENT;
            }

            /* Stage 1: accumulate the row's measured samples. Begin is a
             * configuration check (qualification vs manifest, phase wiring);
             * failure here is a dataset error, not a measurement verdict. */
            if (!MapMeasurementAccumulator_Begin(
                    &accumulator, &input->qualifications.accum,
                    &input->manifest, sector, window)) {
                r.failed_sector = sector;
                r.failed_window = window;
                if (report != 0) *report = r;
                return MAP_PIPELINE_BAD_ARGUMENT;
            }
            for (i = 0u; i < row->sample_count; ++i) {
                r.accum_status = MapMeasurementAccumulator_Add(
                    &accumulator, &row->samples[i]);
                if (r.accum_status != MAP_ACCUM_OK) {
                    r.failed_sector = sector;
                    r.failed_window = window;
                    if (report != 0) *report = r;
                    return MAP_PIPELINE_ACCUM_FAILED;
                }
            }
            r.accum_status = MapMeasurementAccumulator_FinalizeRow(
                &accumulator, &accum_report);
            if (r.accum_status != MAP_ACCUM_OK) {
                r.failed_sector = sector;
                r.failed_window = window;
                if (report != 0) *report = r;
                return MAP_PIPELINE_ACCUM_FAILED;
            }

            /* Stage 2: solve the M matrix for this row from the accepted
             * samples. Only a fully qualified row may contribute coefficients
             * to the artifact. */
            r.solver_status = MapMeasurement_SolveM(
                &accumulator, &input->qualifications.solver,
                &recon[sector][window], &solver_report);
            if (r.solver_status != MAP_SOLVER_OK) {
                r.failed_sector = sector;
                r.failed_window = window;
                if (report != 0) *report = r;
                return MAP_PIPELINE_SOLVER_FAILED;
            }

            /* Stage 3: certify the modulation region bounds from the tested
             * grid cells. An uncertified region must not be serialized. */
            r.cert_status = MapRegionCertify(
                row->cells, row->cell_count, &input->qualifications.region,
                &region[sector][window], &region_report);
            if (r.cert_status != MAP_CERT_OK) {
                r.failed_sector = sector;
                r.failed_window = window;
                if (report != 0) *report = r;
                return MAP_PIPELINE_CERT_FAILED;
            }
        }
    }

    /* Stage 4: assemble and CRC-protect the artifact. The writer re-checks
     * identity/provenance/startup sanity; a failure here means the dataset
     * cannot legally become a map. */
    if (!MapArtifactWriter_Build(
            &input->identity, &input->provenance,
            input->startup_sector, input->startup_window,
            input->startup_hold_cycles, input->startup_mu,
            input->startup_mv, input->startup_mw,
            recon, region, out)) {
        if (report != 0) *report = r;
        return MAP_PIPELINE_BUILD_FAILED;
    }

    r.ready = 1u;
    if (report != 0) *report = r;
    return MAP_PIPELINE_OK;
}
