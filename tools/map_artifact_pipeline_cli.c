/* Host CLI for the L3 measurement pipeline:
 *
 *   map_artifact_pipeline_cli <dataset.txt> [out_dir]
 *
 * Reads a measurement dataset (see map_artifact_pipeline.md for the format),
 * runs Accumulator -> Solver -> Certifier -> Writer for all 12 rows and emits
 * <out_dir>/oew_map_v2.bin (canonical 497-byte wire artifact) plus
 * <out_dir>/oew_map_v2.json (audit metadata). Exit code 0 only when the
 * artifact was produced; any row failure aborts with a message (fail-closed).
 */
#include <stdio.h>
#include <errno.h>
#include <limits.h>
#include <stdlib.h>
#include <string.h>

#include "map_artifact_pipeline.h"

#define CLI_MAX_LINE 512u
#define CLI_MAX_SAMPLES MAP_ACCUM_MAX_SAMPLES_PER_ROW
#define CLI_MAX_CELLS MAP_CERT_MAX_CELLS

static int parse_error(const char *what, unsigned line_no)
{
    fprintf(stderr, "map_artifact_pipeline_cli: parse error at line %u: %s\n",
            line_no, what);
    return 2;
}

static bool token_value(const char *token, const char *key, uint64_t *out)
{
    size_t klen = strlen(key);
    char *end;
    if (strncmp(token, key, klen) != 0 || token[klen] != '=') return false;
    *out = strtoull(token + klen + 1, &end, 0);
    return end != token + klen + 1 && *end == '\0';
}

static bool token_signed(const char *token, const char *key, int64_t *out)
{
    size_t klen = strlen(key);
    char *end;
    long long value;
    if (strncmp(token, key, klen) != 0 || token[klen] != '=') return false;
    errno = 0;
    value = strtoll(token + klen + 1, &end, 0);
    if (end == token + klen + 1 || *end != '\0' || errno == ERANGE) return false;
    *out = (int64_t)value;
    return true;
}

static bool token_i16(const char *token, const char *key, int16_t *out)
{
    int64_t value;
    if (!token_signed(token, key, &value) || value < INT16_MIN || value > INT16_MAX) return false;
    *out = (int16_t)value;
    return true;
}

static bool token_i32(const char *token, const char *key, int32_t *out)
{
    int64_t value;
    if (!token_signed(token, key, &value) || value < INT32_MIN || value > INT32_MAX) return false;
    *out = (int32_t)value;
    return true;
}

static int write_file(const char *path, const uint8_t *data, size_t size)
{
    FILE *f = fopen(path, "wb");
    if (f == 0) {
        fprintf(stderr, "map_artifact_pipeline_cli: cannot write %s\n", path);
        return 0;
    }
    if (size != 0u && fwrite(data, 1, size, f) != size) {
        fclose(f);
        return 0;
    }
    fclose(f);
    return 1;
}

int main(int argc, char **argv)
{
    MapPipelineInput input;
    MapPipelineReport report;
    OewCurrentMap map;
    MapMeasurementSample samples[OEW_CURRENT_MAP_SECTOR_COUNT]
                                [OEW_CURRENT_MAP_WINDOW_COUNT][CLI_MAX_SAMPLES];
    MapGridCell cells[OEW_CURRENT_MAP_SECTOR_COUNT]
                     [OEW_CURRENT_MAP_WINDOW_COUNT][CLI_MAX_CELLS];
    uint16_t sample_counts[OEW_CURRENT_MAP_SECTOR_COUNT]
                          [OEW_CURRENT_MAP_WINDOW_COUNT];
    uint16_t cell_counts[OEW_CURRENT_MAP_SECTOR_COUNT]
                        [OEW_CURRENT_MAP_WINDOW_COUNT];
    uint8_t row_phase_a[OEW_CURRENT_MAP_SECTOR_COUNT]
                       [OEW_CURRENT_MAP_WINDOW_COUNT];
    uint8_t row_phase_b[OEW_CURRENT_MAP_SECTOR_COUNT]
                       [OEW_CURRENT_MAP_WINDOW_COUNT];
    bool row_seen[OEW_CURRENT_MAP_SECTOR_COUNT][OEW_CURRENT_MAP_WINDOW_COUNT];
    char line[CLI_MAX_LINE];
    char out_dir[CLI_MAX_LINE];
    char bin_path[CLI_MAX_LINE + 32u];
    char json_path[CLI_MAX_LINE + 32u];
    uint8_t wire[OEW_CURRENT_MAP_WIRE_SIZE];
    char json[2048];
    size_t n;
    uint64_t total_samples = 0u;
    int current_sector = -1;
    int current_window = -1;
    uint8_t manifest_phase_a = 0u;
    uint8_t manifest_phase_b = 1u;
    int have_identity = 0;
    int have_provenance = 0;
    int have_startup = 0;
    int have_accumq = 0;
    int have_solverq = 0;
    int have_regionq = 0;
    FILE *f;
    unsigned line_no = 0u;
    uint8_t sector;
    uint8_t window;

    if (argc < 2 || argc > 3) {
        fprintf(stderr,
                "usage: map_artifact_pipeline_cli <dataset.txt> [out_dir]\n");
        return 2;
    }
    strncpy(out_dir, argc == 3 ? argv[2] : ".", sizeof(out_dir) - 1u);
    out_dir[sizeof(out_dir) - 1u] = '\0';

    memset(&input, 0, sizeof(input));
    memset(samples, 0, sizeof(samples));
    memset(cells, 0, sizeof(cells));
    memset(sample_counts, 0, sizeof(sample_counts));
    memset(cell_counts, 0, sizeof(cell_counts));
    memset(row_seen, 0, sizeof(row_seen));
    for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
        for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
            input.rows[sector][window].samples = samples[sector][window];
            input.rows[sector][window].cells = cells[sector][window];
            row_phase_a[sector][window] = 0u;
            row_phase_b[sector][window] = 1u;
        }
    }

    f = fopen(argv[1], "r");
    if (f == 0) {
        fprintf(stderr, "map_artifact_pipeline_cli: cannot open %s\n", argv[1]);
        return 2;
    }
    while (fgets(line, (int)sizeof(line), f) != 0) {
        char *token;
        uint64_t v;
        ++line_no;
        token = strtok(line, " \t\r\n");
        if (token == 0 || token[0] == '#') continue;

        if (strcmp(token, "identity") == 0) {
            while ((token = strtok(0, " \t\r\n")) != 0) {
                if (token_value(token, "board", &v)) input.identity.board_revision = (uint16_t)v;
                else if (token_value(token, "pwm", &v)) input.identity.pwm_frequency_hz = (uint32_t)v;
                else if (token_value(token, "arr", &v)) input.identity.timer_arr = (uint32_t)v;
                else if (token_value(token, "trigger", &v)) input.identity.adc_trigger_id = (uint32_t)v;
                else if (token_value(token, "toff", &v)) input.identity.trigger_offset_ticks = (uint16_t)v;
                else if (token_value(token, "dt", &v)) input.identity.deadtime_ticks = (uint16_t)v;
                else if (token_value(token, "clk", &v)) input.identity.adc_clock_hz = (uint32_t)v;
                else if (token_value(token, "smp", &v)) input.identity.adc_sample_cycles_x2 = (uint16_t)v;
                else if (token_value(token, "res", &v)) input.identity.adc_resolution = (uint8_t)v;
                else if (token_value(token, "acs", &v)) input.identity.adc_config_signature = (uint32_t)v;
                else if (token_value(token, "ccs", &v)) input.identity.current_calibration_signature = (uint32_t)v;
                else return parse_error("unknown identity key", line_no);
            }
            have_identity = 1;
        } else if (strcmp(token, "provenance") == 0) {
            while ((token = strtok(0, " \t\r\n")) != 0) {
                if (token_value(token, "cid", &v)) input.provenance.characterization_id = (uint32_t)v;
                else if (token_value(token, "dcrc", &v)) input.provenance.dataset_crc32 = (uint32_t)v;
                else if (token_value(token, "tb", &v)) input.provenance.tool_build_id = (uint32_t)v;
                else if (token_value(token, "qr", &v)) input.provenance.qualification_revision = (uint32_t)v;
                else if (token_value(token, "sr", &v)) input.provenance.solver_revision = (uint32_t)v;
                else if (token_value(token, "cr", &v)) input.provenance.certifier_revision = (uint32_t)v;
                else return parse_error("unknown provenance key", line_no);
            }
            have_provenance = 1;
        } else if (strcmp(token, "startup") == 0) {
            while ((token = strtok(0, " \t\r\n")) != 0) {
                if (token_value(token, "sector", &v)) input.startup_sector = (uint8_t)v;
                else if (token_value(token, "window", &v)) input.startup_window = (uint8_t)v;
                else if (token_value(token, "hold", &v)) input.startup_hold_cycles = (uint16_t)v;
                else if (token_i16(token, "mu", &input.startup_mu)) { }
                else if (token_i16(token, "mv", &input.startup_mv)) { }
                else if (token_i16(token, "mw", &input.startup_mw)) { }
                else return parse_error("unknown startup key", line_no);
            }
            have_startup = 1;
        } else if (strcmp(token, "accumq") == 0) {
            while ((token = strtok(0, " \t\r\n")) != 0) {
                if (token_value(token, "min", &v)) input.qualifications.accum.min_samples = (uint16_t)v;
                else if (token_i32(token, "mad", &input.qualifications.accum.mad_limit_ma)) { }
                else if (token_i32(token, "kcl", &input.qualifications.accum.kcl_limit_ma)) { }
                else if (token_value(token, "margin", &v)) input.qualifications.accum.min_margin_ticks = (uint16_t)v;
                else return parse_error("unknown accumq key", line_no);
            }
            have_accumq = 1;
        } else if (strcmp(token, "solverq") == 0) {
            while ((token = strtok(0, " \t\r\n")) != 0) {
                if (token_value(token, "min", &v)) input.qualifications.solver.min_samples = (uint16_t)v;
                else if (token_value(token, "holdout", &v)) input.qualifications.solver.holdout_samples = (uint16_t)v;
                else if (token_i32(token, "rms", &input.qualifications.solver.residual_rms_limit_ma)) { }
                else if (token_i32(token, "max", &input.qualifications.solver.residual_max_limit_ma)) { }
                else if (token_i32(token, "bias", &input.qualifications.solver.bias_limit_ma)) { }
                else if (token_i32(token, "hrms", &input.qualifications.solver.holdout_rms_limit_ma)) { }
                else if (token_i32(token, "kclrms", &input.qualifications.solver.kcl_rms_limit_ma)) { }
                else if (token_value(token, "cond", &v)) input.qualifications.solver.max_condition_ratio = (uint32_t)v;
                else if (token_value(token, "det", &v)) input.qualifications.solver.min_abs_determinant = (int32_t)v;
                else if (token_value(token, "diag", &v)) input.qualifications.solver.min_abs_diagonal = (int32_t)v;
                else return parse_error("unknown solverq key", line_no);
            }
            have_solverq = 1;
        } else if (strcmp(token, "regionq") == 0) {
            while ((token = strtok(0, " \t\r\n")) != 0) {
                if (token_value(token, "min", &v)) input.qualifications.region.min_valid_cells = (uint16_t)v;
                else if (token_i16(token, "guard", &input.qualifications.region.guard_q15)) { }
                else if (token_value(token, "margin", &v)) input.qualifications.region.min_margin_ticks = (uint16_t)v;
                else if (token_value(token, "use_geometry", &v)) input.qualifications.region.use_geometry_bounds = v != 0u;
                else if (token_i16(token, "w0_mod_min", &input.qualifications.region.geometry_window0_min_mod_q15)) { }
                else if (token_i16(token, "w0_mod_max", &input.qualifications.region.geometry_window0_max_mod_q15)) { }
                else if (token_i16(token, "w1_mod_min", &input.qualifications.region.geometry_window1_min_mod_q15)) { }
                else if (token_i16(token, "w1_mod_max", &input.qualifications.region.geometry_window1_max_mod_q15)) { }
                else return parse_error("unknown regionq key", line_no);
            }
            have_regionq = 1;
        } else if (strcmp(token, "row") == 0) {
            uint64_t rs = 0u, rw = 0u, pa = 0u, pb = 0u;
            if (!have_identity) {
                return parse_error("identity must precede row lines", line_no);
            }
            while ((token = strtok(0, " \t\r\n")) != 0) {
                if (token_value(token, "sector", &v)) rs = v;
                else if (token_value(token, "window", &v)) rw = v;
                else if (token_value(token, "phase_a", &v)) pa = v;
                else if (token_value(token, "phase_b", &v)) pb = v;
                else return parse_error("unknown row key", line_no);
            }
            if (rs >= OEW_CURRENT_MAP_SECTOR_COUNT ||
                rw >= OEW_CURRENT_MAP_WINDOW_COUNT) {
                return parse_error("row sector/window out of range", line_no);
            }
            if (row_seen[rs][rw]) return parse_error("duplicate row", line_no);
            row_seen[rs][rw] = true;
            current_sector = (int)rs;
            current_window = (int)rw;
            row_phase_a[rs][rw] = (uint8_t)pa;
            row_phase_b[rs][rw] = (uint8_t)pb;
        } else if (strcmp(token, "sample") == 0) {
            MapMeasurementSample s;
            uint64_t seq = 0u, margin = 0u;
            int32_t idc1 = 0, idc2 = 0, ict = 0, vbus = 0;
            int32_t refu = 0, refv = 0, refw = 0;
            uint64_t settled = 0u, scope = 0u;
            if (current_sector < 0) return parse_error("sample before row", line_no);
            while ((token = strtok(0, " \t\r\n")) != 0) {
                if (token_value(token, "seq", &v)) seq = v;
                else if (token_i32(token, "idc1", &idc1)) { }
                else if (token_i32(token, "idc2", &idc2)) { }
                else if (token_i32(token, "ict", &ict)) { }
                else if (token_i32(token, "vbus", &vbus)) { }
                else if (token_i32(token, "refu", &refu)) { }
                else if (token_i32(token, "refv", &refv)) { }
                else if (token_i32(token, "refw", &refw)) { }
                else if (token_value(token, "margin", &v)) margin = v;
                else if (token_value(token, "settled", &v)) settled = v;
                else if (token_value(token, "scope", &v)) scope = v;
                else return parse_error("unknown sample key", line_no);
            }
            if (sample_counts[current_sector][current_window] >= CLI_MAX_SAMPLES) {
                return parse_error("too many samples for row", line_no);
            }
            memset(&s, 0, sizeof(s));
            s.capture.capture_id = 1u;
            s.capture.fault_reason = MAP_CAPTURE_OK;
            s.capture.frame.status = ADC_FRAME_WINDOW_INVALID;
            s.capture.frame.sequence = (uint32_t)seq;
            s.capture.frame.tim1_sector = (uint8_t)current_sector;
            s.capture.frame.sample_window = (uint8_t)current_window;
            s.capture.frame.idc1_ma = idc1;
            s.capture.frame.idc2_ma = idc2;
            s.capture.frame.ict_ma = ict;
            s.capture.frame.vbus_mv = vbus;
            /* Capture PWM identity must match the manifest (derived from the
             * dataset identity): the accumulator validates every sample. */
            s.capture.pwm.tim1_arr = input.identity.timer_arr;
            s.capture.pwm.pwm_frequency_hz = input.identity.pwm_frequency_hz;
            s.capture.pwm.trigger_revision = input.identity.adc_trigger_id;
            s.capture.pwm.deadtime_ticks =
                (uint8_t)input.identity.deadtime_ticks;
            s.reference.valid = 1u;
            s.reference.source = MAP_REFERENCE_SOURCE_SCOPE;
            s.reference.sample_id = (uint32_t)seq;
            s.reference.phase_u_ma = refu;
            s.reference.phase_v_ma = refv;
            s.reference.phase_w_ma = refw;
            s.timing.adc_settled = (uint8_t)settled;
            s.timing.scope_qualified = (uint8_t)scope;
            s.timing.margin_ticks = (uint16_t)margin;
            samples[current_sector][current_window][
                sample_counts[current_sector][current_window]++] = s;
            ++total_samples;
        } else if (strcmp(token, "cell") == 0) {
            MapGridCell c;
            int16_t mu = 0, mv = 0, mw = 0;
            uint64_t margin = 0u, status = 0u;
            if (current_sector < 0) return parse_error("cell before row", line_no);
            while ((token = strtok(0, " \t\r\n")) != 0) {
                {
                    int16_t signed_value;
                    if (token_i16(token, "mu", &signed_value)) mu = signed_value;
                    else if (token_i16(token, "mv", &signed_value)) mv = signed_value;
                    else if (token_i16(token, "mw", &signed_value)) mw = signed_value;
                    else if (token_value(token, "margin", &v)) margin = v;
                    else if (token_value(token, "status", &v)) status = v;
                    else return parse_error("unknown cell key", line_no);
                    continue;
                }
            }
            if (cell_counts[current_sector][current_window] >= CLI_MAX_CELLS) {
                return parse_error("too many cells for row", line_no);
            }
            memset(&c, 0, sizeof(c));
            c.mu = mu;
            c.mv = mv;
            c.mw = mw;
            c.margin_ticks = (uint16_t)margin;
            c.status = (uint8_t)status;
            cells[current_sector][current_window][
                cell_counts[current_sector][current_window]++] = c;
        } else {
            return parse_error("unknown command", line_no);
        }
    }
    fclose(f);

    if (!have_identity || !have_provenance || !have_startup ||
        !have_accumq || !have_solverq || !have_regionq) {
        fprintf(stderr, "map_artifact_pipeline_cli: dataset incomplete "
                        "(identity/provenance/startup/qualifications)\n");
        return 2;
    }

    /* Manifest is derived from the campaign identity and the phase wiring of
     * the first row; all rows must agree on phase_a/phase_b. */
    input.manifest.magic = MAP_REFERENCE_MAGIC;
    input.manifest.revision = MAP_REFERENCE_REVISION;
    input.manifest.board_revision = input.identity.board_revision;
    input.manifest.pwm_frequency_hz = input.identity.pwm_frequency_hz;
    input.manifest.timer_arr = input.identity.timer_arr;
    input.manifest.adc_trigger_id = input.identity.adc_trigger_id;
    input.manifest.adc_clock_hz = input.identity.adc_clock_hz;
    input.manifest.adc_sample_cycles_x2 = input.identity.adc_sample_cycles_x2;
    input.manifest.adc_resolution = input.identity.adc_resolution;
    input.manifest.deadtime_ticks = (uint8_t)input.identity.deadtime_ticks;
    input.manifest.source = MAP_REFERENCE_SOURCE_SCOPE;
    input.manifest.tool_build_id = input.provenance.tool_build_id;
    input.manifest.record_count = (uint32_t)total_samples;
    for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
        for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
            if (!row_seen[sector][window]) {
                fprintf(stderr, "map_artifact_pipeline_cli: missing row %u/%u\n",
                        sector, window);
                return 2;
            }
            if (sample_counts[sector][window] == 0u ||
                cell_counts[sector][window] == 0u) {
                fprintf(stderr, "map_artifact_pipeline_cli: row %u/%u has no "
                                "samples or cells\n", sector, window);
                return 2;
            }
            input.rows[sector][window].sample_count = sample_counts[sector][window];
            input.rows[sector][window].cell_count = cell_counts[sector][window];
            input.qualifications.accum.phase_a = row_phase_a[sector][window];
            input.qualifications.accum.phase_b = row_phase_b[sector][window];
            if (sector == 0u && window == 0u) {
                manifest_phase_a = row_phase_a[sector][window];
                manifest_phase_b = row_phase_b[sector][window];
            } else if (row_phase_a[sector][window] != manifest_phase_a ||
                       row_phase_b[sector][window] != manifest_phase_b) {
                fprintf(stderr, "map_artifact_pipeline_cli: phase wiring must "
                                "be identical across rows\n");
                return 2;
            }
        }
    }
    input.manifest.phase_a = manifest_phase_a;
    input.manifest.phase_b = manifest_phase_b;
    input.manifest.crc32 = MapReferenceManifest_CalculateCrc32(&input.manifest);

    /* Run the pipeline. */
    {
        MapPipelineStatus status = MapArtifactPipeline_Run(&input, &map, &report);
        if (status != MAP_PIPELINE_OK) {
            fprintf(stderr, "map_artifact_pipeline_cli: pipeline failed "
                            "(status %d", (int)status);
            if (report.ready == 0u &&
                (status == MAP_PIPELINE_ACCUM_FAILED ||
                 status == MAP_PIPELINE_SOLVER_FAILED ||
                 status == MAP_PIPELINE_CERT_FAILED ||
                 status == MAP_PIPELINE_BAD_ARGUMENT)) {
                fprintf(stderr, ", row %u/%u", report.failed_sector,
                        report.failed_window);
            }
            fprintf(stderr, ")\n");
            return 1;
        }
    }

    /* The artifact must be loadable by the firmware consumer before it is
     * emitted. The loader applies structural checks beyond the pipeline's
     * per-row gates: full identity match, region overlap/coverage, startup
     * containment. A campaign that cannot become a map is rejected here. */
    if (!CurrentMap_LoadMeasured(&map, &input.identity)) {
        fprintf(stderr, "map_artifact_pipeline_cli: artifact rejected by "
                        "CurrentMap_LoadMeasured (structural/identity)\n");
        return 1;
    }

    n = MapArtifactWriter_EncodeBinary(&map, wire, sizeof(wire));
    if (n != OEW_CURRENT_MAP_WIRE_SIZE) {
        fprintf(stderr, "map_artifact_pipeline_cli: encoder rejected artifact\n");
        return 1;
    }
    snprintf(bin_path, sizeof(bin_path), "%s/oew_map_v2.bin", out_dir);
    snprintf(json_path, sizeof(json_path), "%s/oew_map_v2.json", out_dir);
    if (!write_file(bin_path, wire, n)) return 1;
    {
        size_t json_size = MapArtifactWriter_EncodeAuditJson(&map, json, sizeof(json));
        if (json_size == 0u || !write_file(json_path, (const uint8_t *)json, json_size)) {
            return 1;
        }
        printf("map_artifact_pipeline_cli: %s (%u bytes), %s (%u bytes)\n",
               bin_path, (unsigned)n, json_path, (unsigned)json_size);
    }
    return 0;
}
