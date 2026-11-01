/* Офлайн-покрытие карты токов OEW по модуляционному пространству.
 *
 * Прогоняет штатный CurrentMap_SelectNextContext по сетке «угол × амплитуда»
 * и выдаёт, где вектор модуляции попадает ровно в один регион карты.
 *
 * Сборка и запуск из корня репозитория:
 *   gcc -std=c99 -O2 -Wall -Wextra -Werror -Isrc src/map_artifact_decoder.c \
 *       src/current_map_selector.c src/current_reconstruct.c tests/adc_frame_stub.c \
 *       tools/map_coverage.c -lm -o build/map_coverage.exe
 *   build/map_coverage.exe [artifact.bin] [out.csv] > summary.txt
 *
 * Сетка (фиксирована, чтобы числа сверялись между машинами):
 *   phi = 0.0 .. 359.9 град, шаг 0.1 (3600 углов), phi = 0 -> максимум mu;
 *   A   = 0, 64, ..., 32064 и 32112 (503 амплитуды, Q15 модуляции);
 *   mu = round(A cos phi), mv = round(A cos(phi - 120)), mw = round(A cos(phi + 120)),
 *   округление каждой фазы отдельно (half away from zero), clamp +-32112,
 *   нулевая сумма не форсируется — как на входе селектора в foc.c.
 *
 * CSV: phi_deg,A,r_cov,first_gap,matches — строка на каждую точку сетки.
 *   matches   — число регионов, содержащих вектор (1 = покрыт, 0 = дыра,
 *               >=2 = пересечение, загрузчик обязан был его отклонить);
 *   first_gap — наименьшая A на этом угле с matches != 1, -1 если дыр нет;
 *   r_cov     — наибольшая A, при которой все A' <= A покрыты; -1 если не
 *               покрыт даже нулевой вектор.
 */
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "current_map_selector.h"
#include "map_artifact_decoder.h"

#define DEFAULT_ARTIFACT "docs/map_artifacts/20261014_pc3/oew_map_v2.bin"
#define DEFAULT_CSV      "build/map_coverage.csv"

#define PHI_STEPS   3600
#define A_STEP      64
#define A_MAX       32112
#define A_COUNT     503
#define R_TARGET    19661   /* 0.6 в Q15 */

static const double kPi = 3.14159265358979323846;

/* ── SHA-256 (FIPS 180-4) — для сверки артефакта с README кампании ─────── */
static const uint32_t K256[64] = {
    0x428a2f98u,0x71374491u,0xb5c0fbcfu,0xe9b5dba5u,0x3956c25bu,0x59f111f1u,0x923f82a4u,0xab1c5ed5u,
    0xd807aa98u,0x12835b01u,0x243185beu,0x550c7dc3u,0x72be5d74u,0x80deb1feu,0x9bdc06a7u,0xc19bf174u,
    0xe49b69c1u,0xefbe4786u,0x0fc19dc6u,0x240ca1ccu,0x2de92c6fu,0x4a7484aau,0x5cb0a9dcu,0x76f988dau,
    0x983e5152u,0xa831c66du,0xb00327c8u,0xbf597fc7u,0xc6e00bf3u,0xd5a79147u,0x06ca6351u,0x14292967u,
    0x27b70a85u,0x2e1b2138u,0x4d2c6dfcu,0x53380d13u,0x650a7354u,0x766a0abbu,0x81c2c92eu,0x92722c85u,
    0xa2bfe8a1u,0xa81a664bu,0xc24b8b70u,0xc76c51a3u,0xd192e819u,0xd6990624u,0xf40e3585u,0x106aa070u,
    0x19a4c116u,0x1e376c08u,0x2748774cu,0x34b0bcb5u,0x391c0cb3u,0x4ed8aa4au,0x5b9cca4fu,0x682e6ff3u,
    0x748f82eeu,0x78a5636fu,0x84c87814u,0x8cc70208u,0x90befffau,0xa4506cebu,0xbef9a3f7u,0xc67178f2u
};

static uint32_t ror32(uint32_t x, unsigned n) { return (x >> n) | (x << (32u - n)); }

static void sha256_block(uint32_t h[8], const uint8_t *p)
{
    uint32_t w[64], a, b, c, d, e, f, g, k, t1, t2;
    unsigned i;
    for (i = 0; i < 16; ++i)
        w[i] = ((uint32_t)p[4*i] << 24) | ((uint32_t)p[4*i+1] << 16) |
               ((uint32_t)p[4*i+2] << 8) | p[4*i+3];
    for (i = 16; i < 64; ++i)
        w[i] = w[i-16] + (ror32(w[i-15],7) ^ ror32(w[i-15],18) ^ (w[i-15] >> 3)) +
               w[i-7] + (ror32(w[i-2],17) ^ ror32(w[i-2],19) ^ (w[i-2] >> 10));
    a = h[0]; b = h[1]; c = h[2]; d = h[3]; e = h[4]; f = h[5]; g = h[6]; k = h[7];
    for (i = 0; i < 64; ++i) {
        t1 = k + (ror32(e,6) ^ ror32(e,11) ^ ror32(e,25)) + ((e & f) ^ (~e & g)) + K256[i] + w[i];
        t2 = (ror32(a,2) ^ ror32(a,13) ^ ror32(a,22)) + ((a & b) ^ (a & c) ^ (b & c));
        k = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
    }
    h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += k;
}

static void sha256_hex(const uint8_t *data, size_t len, char out[65])
{
    uint32_t h[8] = { 0x6a09e667u,0xbb67ae85u,0x3c6ef372u,0xa54ff53au,
                      0x510e527fu,0x9b05688cu,0x1f83d9abu,0x5be0cd19u };
    uint8_t tail[128];
    size_t full = len / 64u, rem = len % 64u, tail_len, i;
    uint64_t bits = (uint64_t)len * 8u;

    for (i = 0; i < full; ++i) sha256_block(h, data + 64u * i);
    memset(tail, 0, sizeof(tail));
    memcpy(tail, data + 64u * full, rem);
    tail[rem] = 0x80u;
    tail_len = (rem < 56u) ? 64u : 128u;
    for (i = 0; i < 8; ++i) tail[tail_len - 1u - i] = (uint8_t)(bits >> (8u * i));
    for (i = 0; i < tail_len; i += 64u) sha256_block(h, tail + i);
    for (i = 0; i < 8; ++i) sprintf(out + 8u * i, "%08lx", (unsigned long)h[i]);
}

/* ── Покрытие ─────────────────────────────────────────────────────────── */
static OewPwmRegion regions[OEW_CURRENT_MAP_SECTOR_COUNT][OEW_CURRENT_MAP_WINDOW_COUNT];

static int16_t q15_phase(double a, double phi_rad)
{
    long v = lround(a * cos(phi_rad));
    if (v > A_MAX) v = A_MAX;
    if (v < -A_MAX) v = -A_MAX;
    return (int16_t)v;
}

static unsigned count_matches(int16_t mu, int16_t mv, int16_t mw)
{
    unsigned s, w, n = 0u;
    for (s = 0; s < OEW_CURRENT_MAP_SECTOR_COUNT; ++s)
        for (w = 0; w < OEW_CURRENT_MAP_WINDOW_COUNT; ++w) {
            const OewPwmRegion *r = &regions[s][w];
            if (r->valid && r->min_margin_ticks != 0u &&
                mu >= r->mu_min && mu <= r->mu_max &&
                mv >= r->mv_min && mv <= r->mv_max &&
                mw >= r->mw_min && mw <= r->mw_max)
                ++n;
        }
    return n;
}

static int amplitude(int k) { return (k < A_COUNT - 1) ? k * A_STEP : A_MAX; }

int main(int argc, char **argv)
{
    const char *path = (argc > 1) ? argv[1] : DEFAULT_ARTIFACT;
    const char *csv_path = (argc > 2) ? argv[2] : DEFAULT_CSV;
    static uint8_t matches[PHI_STEPS][A_COUNT];
    static int r_cov[PHI_STEPS], first_gap[PHI_STEPS];
    uint8_t wire[OEW_CURRENT_MAP_WIRE_SIZE];
    OewCurrentMap map;
    OewMapIdentity id;
    char sha[65];
    FILE *f;
    size_t got;
    int p, k, extra;
    unsigned s, w;
    long holes = 0, overlaps = 0, islands = 0, selector_mismatch = 0, angles_ok = 0;
    int min_r = A_MAX + 1, min_phi = -1;
    int sector_min[6];

    f = fopen(path, "rb");
    if (f == 0) { fprintf(stderr, "cannot open %s\n", path); return 2; }
    got = fread(wire, 1u, sizeof(wire), f);
    extra = fgetc(f);
    fclose(f);
    if (got != sizeof(wire) || extra != EOF) {
        fprintf(stderr, "%s: expected exactly %u bytes\n", path, (unsigned)sizeof(wire));
        return 2;
    }
    sha256_hex(wire, sizeof(wire), sha);
    if (!MapArtifact_DecodeBinary(wire, sizeof(wire), &map)) {
        fprintf(stderr, "%s: decode failed (magic/revision/CRC)\n", path);
        return 2;
    }

    /* Геометрию проверяем на identity самой карты: привязку к железу
     * проверяет golden-тест, здесь важны только регионы. */
    memset(&id, 0, sizeof(id));
    id.board_revision = map.board_revision;
    id.pwm_frequency_hz = map.pwm_frequency_hz;
    id.timer_arr = map.timer_arr;
    id.adc_trigger_id = map.adc_trigger_id;
    id.trigger_offset_ticks = map.trigger_offset_ticks;
    id.deadtime_ticks = map.deadtime_ticks;
    id.adc_clock_hz = map.adc_clock_hz;
    id.adc_sample_cycles_x2 = map.adc_sample_cycles_x2;
    id.adc_resolution = map.adc_resolution;
    id.adc_config_signature = map.adc_config_signature;
    id.current_calibration_signature = map.current_calibration_signature;
    CurrentMap_Reset();
    if (!CurrentMap_LoadMeasured(&map, &id)) {
        fprintf(stderr, "%s: CurrentMap_LoadMeasured rejected the map\n", path);
        return 2;
    }
    for (s = 0; s < OEW_CURRENT_MAP_SECTOR_COUNT; ++s)
        for (w = 0; w < OEW_CURRENT_MAP_WINDOW_COUNT; ++w)
            regions[s][w] = map.region[s][w];

    for (s = 0; s < 6u; ++s) sector_min[s] = A_MAX + 1;

    for (p = 0; p < PHI_STEPS; ++p) {
        const double phi = (double)p * kPi / 1800.0;
        int kgap = -1;
        first_gap[p] = -1;
        for (k = 0; k < A_COUNT; ++k) {
            const double a = (double)amplitude(k);
            const int16_t mu = q15_phase(a, phi);
            const int16_t mv = q15_phase(a, phi - 2.0 * kPi / 3.0);
            const int16_t mw = q15_phase(a, phi + 2.0 * kPi / 3.0);
            PwmSampleContext ctx;
            const unsigned n = count_matches(mu, mv, mw);
            const bool sel = CurrentMap_SelectNextContext(mu, mv, mw, &ctx);

            matches[p][k] = (uint8_t)(n > 255u ? 255u : n);
            if (sel != (n == 1u)) ++selector_mismatch;
            if (n == 0u) ++holes;
            if (n >= 2u) ++overlaps;
            if (n != 1u && first_gap[p] < 0) { first_gap[p] = amplitude(k); kgap = k; }
            if (n == 1u && first_gap[p] >= 0) ++islands;
        }
        if (first_gap[p] < 0) r_cov[p] = A_MAX;
        else if (kgap == 0) r_cov[p] = -1;
        else r_cov[p] = amplitude(kgap - 1);  /* предыдущая точка сетки */
        if (r_cov[p] < min_r) { min_r = r_cov[p]; min_phi = p; }
        if (r_cov[p] >= R_TARGET) ++angles_ok;
        if (r_cov[p] < sector_min[p / 600]) sector_min[p / 600] = r_cov[p];
    }

    f = fopen(csv_path, "w");
    if (f == 0) { fprintf(stderr, "cannot write %s\n", csv_path); return 2; }
    fputs("phi_deg,A,r_cov,first_gap,matches\n", f);
    for (p = 0; p < PHI_STEPS; ++p)
        for (k = 0; k < A_COUNT; ++k)
            fprintf(f, "%d.%d,%d,%d,%d,%u\n", p / 10, p % 10, amplitude(k),
                    r_cov[p], first_gap[p], (unsigned)matches[p][k]);
    fclose(f);

    printf("map_coverage\n");
    printf("artifact=%s\n", path);
    printf("artifact_sha256=%s\n", sha);
    printf("map_crc32=%08lX\n", (unsigned long)map.crc32);
    printf("grid=phi 0.0..359.9 step 0.1 deg; A 0..%d step %d + %d (Q15 mod)\n",
           A_MAX - (A_MAX % A_STEP), A_STEP, A_MAX);
    printf("csv=%s\n", csv_path);
    printf("min_r_cov=%d (%.4f)\n", min_r, (double)min_r / 32768.0);
    printf("min_r_cov_phi_deg=%d.%d\n", min_phi / 10, min_phi % 10);
    printf("angles_r_cov_ge_0.6=%.4f (%ld/%d)\n",
           (double)angles_ok / PHI_STEPS, angles_ok, PHI_STEPS);
    printf("zero_vector_matches=%u\n", count_matches(0, 0, 0));
    printf("holes=%ld overlaps=%ld islands_beyond_first_gap=%ld selector_mismatch=%ld\n",
           holes, overlaps, islands, selector_mismatch);
    for (s = 0; s < 6u; ++s)
        printf("sector_phi_%u_%u_min_r_cov=%d (%.4f)\n", 60u * s, 60u * s + 60u,
               sector_min[s], (double)sector_min[s] / 32768.0);
    for (s = 0; s < OEW_CURRENT_MAP_SECTOR_COUNT; ++s)
        for (w = 0; w < OEW_CURRENT_MAP_WINDOW_COUNT; ++w) {
            const OewPwmRegion *r = &regions[s][w];
            printf("region s=%u w=%u mu=[%d,%d] mv=[%d,%d] mw=[%d,%d] margin=%u\n",
                   s, w, r->mu_min, r->mu_max, r->mv_min, r->mv_max,
                   r->mw_min, r->mw_max, (unsigned)r->min_margin_ticks);
        }
    return selector_mismatch == 0 ? 0 : 1;
}
