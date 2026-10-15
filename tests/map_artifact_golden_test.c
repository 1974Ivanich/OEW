/* Golden-тест артефакта карты: закоммиченный файл кампании обязан и дальше
 * декодироваться прошивочным декодером и приниматься CurrentMap_LoadMeasured
 * с identity из манифеста кампании.
 *
 * Фикстура: docs/map_artifacts/20261014_pc3/oew_map_v2.bin (497 Б,
 * sha256=ddb2cd6091236c5c2563ee94b86ab8f7fb1821b8b00e0923bdeef2e79e363d83),
 * собрана tools/map_artifact_pipeline_cli.exe по кампании map_campaign_20261013
 * (96 точек, 739 сэмплов).
 *
 * Ожидаемые значения вписаны константами по campaign_manifest.json /
 * oew_map_v2.json этого каталога, а не вычислены из самого файла: тест обязан
 * падать, если артефакт пересобрали/подменили или если декодер/загрузчик
 * изменил приём без переприёмки. Пересобрали намеренно — обновите пины по
 * новому манифесту (crc32 в oew_map_v2.json) и прогоните `make test-hosted`.
 *
 * Запуск из корня репозитория (так его гоняет CI); путь к файлу можно
 * переопределить первым аргументом.
 */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "map_artifact_decoder.h"
#include "current_map_selector.h"

#define GOLDEN_PATH "docs/map_artifacts/20261014_pc3/oew_map_v2.bin"

/* campaign_manifest.json -------------------------------------------------- */
#define GOLD_BOARD_REVISION        7u
#define GOLD_PWM_FREQUENCY_HZ      5000u
#define GOLD_TIMER_ARR             999u
#define GOLD_ADC_TRIGGER_ID        1329944369u /* 0x4F455731 */
#define GOLD_TRIGGER_OFFSET_TICKS  0u
#define GOLD_DEADTIME_TICKS        192u
#define GOLD_ADC_CLOCK_HZ          42500000u
#define GOLD_ADC_SAMPLE_CYCLES_X2  1281u
#define GOLD_ADC_RESOLUTION        0u
#define GOLD_ADC_CONFIG_SIGNATURE  649705851u  /* 0x26B9B97B */
#define GOLD_CAL_SIGNATURE         324348690u  /* 0x13552B12 */

#define GOLD_CHARACTERIZATION_ID   1112490322u /* 0x424F4152 "BOAR": БАЗА id */
#define GOLD_DATASET_CRC32         3668223010u /* 0xDAA4A422 */
#define GOLD_TOOL_BUILD_ID         539363632u  /* 0x20260930 */
#define GOLD_QUALIFICATION_REV     1u
#define GOLD_SOLVER_REV            1u
#define GOLD_CERTIFIER_REV         1u

#define GOLD_STARTUP_SECTOR        0u
#define GOLD_STARTUP_WINDOW        0u
#define GOLD_STARTUP_HOLD_CYCLES   1u
#define GOLD_STARTUP_MU            8192
#define GOLD_STARTUP_MV            8192
#define GOLD_STARTUP_MW            (-16384)
#define GOLD_PHASE_A               0u
#define GOLD_PHASE_B               1u

/* oew_map_v2.json: crc32 карты (пинит содержимое всей структуры — смена
 * любого поля в файле меняет это число). */
#define GOLD_CRC32                 0xF68D948Bu /* 4136473739 */

static int failures;

static bool check(bool condition, const char *what)
{
    if (!condition) {
        fprintf(stderr, "  FAIL: %s\n", what);
        ++failures;
    }
    return condition;
}

static void check_u32(const char *what, uint32_t expected, uint32_t actual)
{
    if (expected != actual) {
        fprintf(stderr,
                "  FAIL: %s: expected 0x%08lX (%lu), got 0x%08lX (%lu)\n",
                what, (unsigned long)expected, (unsigned long)expected,
                (unsigned long)actual, (unsigned long)actual);
        ++failures;
    }
}

static void check_i32(const char *what, int32_t expected, int32_t actual)
{
    if (expected != actual) {
        fprintf(stderr, "  FAIL: %s: expected %ld, got %ld\n",
                what, (long)expected, (long)actual);
        ++failures;
    }
}

static void fail_cell(const char *what, uint8_t sector, uint8_t window)
{
    fprintf(stderr, "  FAIL: %s at sector %u / window %u\n", what,
            (unsigned)sector, (unsigned)window);
    ++failures;
}

static void section_result(const char *name, int failures_before)
{
    printf("  %s: %s\n", name, failures == failures_before ? "PASS" : "FAIL");
}

static bool read_wire(const char *path, uint8_t *buf, size_t size)
{
    FILE *file = fopen(path, "rb");
    size_t got;
    int extra;

    if (file == 0) {
        fprintf(stderr, "  FAIL: cannot open %s (run from repo root)\n", path);
        ++failures;
        return false;
    }
    got = fread(buf, 1u, size, file);
    extra = fgetc(file);
    fclose(file);
    if (got != size || extra != EOF) {
        fprintf(stderr,
                "  FAIL: %s: expected exactly %lu bytes, read %lu (extra byte: %d)\n",
                path, (unsigned long)size, (unsigned long)got, extra);
        ++failures;
        return false;
    }
    return true;
}

static void identity_from_pins(OewMapIdentity *identity)
{
    memset(identity, 0, sizeof(*identity));
    identity->board_revision = GOLD_BOARD_REVISION;
    identity->pwm_frequency_hz = GOLD_PWM_FREQUENCY_HZ;
    identity->timer_arr = GOLD_TIMER_ARR;
    identity->adc_trigger_id = GOLD_ADC_TRIGGER_ID;
    identity->trigger_offset_ticks = GOLD_TRIGGER_OFFSET_TICKS;
    identity->deadtime_ticks = GOLD_DEADTIME_TICKS;
    identity->adc_clock_hz = GOLD_ADC_CLOCK_HZ;
    identity->adc_sample_cycles_x2 = GOLD_ADC_SAMPLE_CYCLES_X2;
    identity->adc_resolution = GOLD_ADC_RESOLUTION;
    identity->adc_config_signature = GOLD_ADC_CONFIG_SIGNATURE;
    identity->current_calibration_signature = GOLD_CAL_SIGNATURE;
}

int main(int argc, char **argv)
{
    const char *path = (argc > 1) ? argv[1] : GOLDEN_PATH;
    uint8_t wire[OEW_CURRENT_MAP_WIRE_SIZE];
    uint8_t tampered[OEW_CURRENT_MAP_WIRE_SIZE];
    OewCurrentMap map;
    OewCurrentMap scratch;
    OewMapIdentity identity;
    PwmSampleContext context;
    uint32_t valid_rows = 0u;
    uint32_t valid_regions = 0u;
    uint8_t sector;
    uint8_t window;
    int16_t mu = 0;
    int16_t mv = 0;
    int16_t mw = 0;
    int failures_before;

    puts("map_artifact_golden_test:");
    printf("  fixture: %s\n", path);

    failures_before = failures;
    if (!read_wire(path, wire, sizeof(wire)))
        goto done;

    if (!check(MapArtifact_DecodeBinary(wire, sizeof(wire), &map),
               "committed artifact does not decode (size/magic/revision/CRC)"))
        goto done;

    check_u32("magic", OEW_CURRENT_MAP_MAGIC, map.magic);
    check_u32("revision", OEW_CURRENT_MAP_REVISION, (uint32_t)map.revision);
    check_u32("board_revision", GOLD_BOARD_REVISION, (uint32_t)map.board_revision);
    check_u32("pwm_frequency_hz", GOLD_PWM_FREQUENCY_HZ, map.pwm_frequency_hz);
    check_u32("timer_arr", GOLD_TIMER_ARR, map.timer_arr);
    check_u32("adc_trigger_id", GOLD_ADC_TRIGGER_ID, map.adc_trigger_id);
    check_u32("trigger_offset_ticks", GOLD_TRIGGER_OFFSET_TICKS,
              (uint32_t)map.trigger_offset_ticks);
    check_u32("deadtime_ticks", GOLD_DEADTIME_TICKS, (uint32_t)map.deadtime_ticks);
    check_u32("adc_clock_hz", GOLD_ADC_CLOCK_HZ, map.adc_clock_hz);
    check_u32("adc_sample_cycles_x2", GOLD_ADC_SAMPLE_CYCLES_X2,
              (uint32_t)map.adc_sample_cycles_x2);
    check_u32("adc_resolution", GOLD_ADC_RESOLUTION, (uint32_t)map.adc_resolution);
    check_u32("adc_config_signature", GOLD_ADC_CONFIG_SIGNATURE,
              map.adc_config_signature);
    check_u32("current_calibration_signature", GOLD_CAL_SIGNATURE,
              map.current_calibration_signature);
    check_u32("provenance.characterization_id", GOLD_CHARACTERIZATION_ID,
              map.provenance.characterization_id);
    check_u32("provenance.dataset_crc32", GOLD_DATASET_CRC32,
              map.provenance.dataset_crc32);
    check_u32("provenance.tool_build_id", GOLD_TOOL_BUILD_ID,
              map.provenance.tool_build_id);
    check_u32("provenance.qualification_revision", GOLD_QUALIFICATION_REV,
              map.provenance.qualification_revision);
    check_u32("provenance.solver_revision", GOLD_SOLVER_REV,
              map.provenance.solver_revision);
    check_u32("provenance.certifier_revision", GOLD_CERTIFIER_REV,
              map.provenance.certifier_revision);
    check_u32("startup_sector", GOLD_STARTUP_SECTOR, (uint32_t)map.startup_sector);
    check_u32("startup_window", GOLD_STARTUP_WINDOW, (uint32_t)map.startup_window);
    check_u32("startup_hold_cycles", GOLD_STARTUP_HOLD_CYCLES,
              (uint32_t)map.startup_hold_cycles);
    check_i32("startup_mu", GOLD_STARTUP_MU, map.startup_mu);
    check_i32("startup_mv", GOLD_STARTUP_MV, map.startup_mv);
    check_i32("startup_mw", GOLD_STARTUP_MW, map.startup_mw);
    check_u32("crc32 (oew_map_v2.json)", GOLD_CRC32, map.crc32);

    for (sector = 0u; sector < OEW_CURRENT_MAP_SECTOR_COUNT; ++sector) {
        for (window = 0u; window < OEW_CURRENT_MAP_WINDOW_COUNT; ++window) {
            const CurrentReconEntry *recon = &map.recon[sector][window];
            const OewPwmRegion *region = &map.region[sector][window];

            if (recon->valid)
                ++valid_rows;
            if (region->valid != 0u)
                ++valid_regions;

            if (!recon->valid)
                fail_cell("recon row not valid", sector, window);
            if (recon->phase_a != GOLD_PHASE_A)
                fail_cell("recon phase_a differs from manifest", sector, window);
            if (recon->phase_b != GOLD_PHASE_B)
                fail_cell("recon phase_b differs from manifest", sector, window);
            if (region->valid == 0u)
                fail_cell("region not valid", sector, window);
            if (region->min_margin_ticks == 0u)
                fail_cell("region min_margin_ticks == 0", sector, window);
            if (region->mu_min > region->mu_max ||
                region->mv_min > region->mv_max ||
                region->mw_min > region->mw_max)
                fail_cell("region bounds inverted", sector, window);
        }
    }
    check_u32("valid recon rows", OEW_CURRENT_MAP_SECTOR_COUNT *
              OEW_CURRENT_MAP_WINDOW_COUNT, valid_rows);
    check_u32("valid regions", OEW_CURRENT_MAP_SECTOR_COUNT *
              OEW_CURRENT_MAP_WINDOW_COUNT, valid_regions);
    section_result("decode + fixture pins", failures_before);

    /* Адмиссия ровно тем же вызовом, что и в прошивке: identity берётся из
     * манифеста кампании, не из декодированной карты. */
    failures_before = failures;
    identity_from_pins(&identity);
    CurrentMap_Reset();
    if (check(CurrentMap_LoadMeasured(&map, &identity),
              "CurrentMap_LoadMeasured rejected the committed artifact")) {
        check(CurrentMap_IsReady(), "map not active after load");
        check_u32("active crc32", GOLD_CRC32, CurrentMap_GetCrc32());
        check_u32("startup hold cycles", GOLD_STARTUP_HOLD_CYCLES,
                  (uint32_t)CurrentMap_GetStartupHoldCycles());
    } else {
        check(!CurrentMap_IsReady(), "map must not stay active after rejection");
    }
    section_result("CurrentMap_LoadMeasured (manifest identity)", failures_before);

    failures_before = failures;
    if (CurrentMap_IsReady()) {
        if (check(CurrentMap_SelectInitialStartupContext(&context, &mu, &mv, &mw),
                  "CurrentMap_SelectInitialStartupContext failed")) {
            check_u32("context sector", GOLD_STARTUP_SECTOR,
                      (uint32_t)context.sector);
            check_u32("context window", GOLD_STARTUP_WINDOW,
                      (uint32_t)context.window);
            check(context.valid, "context not valid");
            check_i32("selected mu", GOLD_STARTUP_MU, mu);
            check_i32("selected mv", GOLD_STARTUP_MV, mv);
            check_i32("selected mw", GOLD_STARTUP_MW, mw);
        }
        if (check(CurrentMap_SelectNextContext(GOLD_STARTUP_MU, GOLD_STARTUP_MV,
                                               GOLD_STARTUP_MW, &context),
                  "SelectNextContext did not find exactly one region for startup vector")) {
            check_u32("next sector", GOLD_STARTUP_SECTOR, (uint32_t)context.sector);
            check_u32("next window", GOLD_STARTUP_WINDOW, (uint32_t)context.window);
        }
    } else {
        puts("  startup context: SKIP (map not active)");
    }
    section_result("startup context", failures_before);

    /* Артефакт привязан к живой identity: подмена deadtime — отказ. */
    failures_before = failures;
    identity.deadtime_ticks = (uint16_t)(GOLD_DEADTIME_TICKS + 1u);
    check(!CurrentMap_LoadMeasured(&map, &identity),
          "artifact admitted with wrong identity (deadtime+1)");
    section_result("identity binding", failures_before);

    /* Байтовая мутация обязана падать на CRC: доказывает, что читается
     * именно файл-фикстура, а не копия в памяти. */
    failures_before = failures;
    memcpy(tampered, wire, sizeof(tampered));
    tampered[OEW_CURRENT_MAP_WIRE_SIZE / 2u] ^= 0x01u;
    check(!MapArtifact_DecodeBinary(tampered, sizeof(tampered), &scratch),
          "mutated byte decoded");
    section_result("byte mutation rejected", failures_before);

done:
    if (failures != 0) {
        fprintf(stderr, "map_artifact_golden_test: FAIL (%d)\n", failures);
        return 1;
    }
    puts("map_artifact_golden_test: PASS");
    return 0;
}
