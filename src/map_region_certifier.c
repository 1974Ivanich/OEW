#include "map_region_certifier.h"

#include <limits.h>
#include <string.h>

#define GEOMETRY_DEFAULT_W0_MIN 6000
#define GEOMETRY_DEFAULT_W0_MAX 10000
#define GEOMETRY_DEFAULT_W1_MIN 10000
#define GEOMETRY_DEFAULT_W1_MAX 14000

static int16_t min_i16(int16_t a, int16_t b) { return a < b ? a : b; }
static int16_t max_i16(int16_t a, int16_t b) { return a > b ? a : b; }

static int32_t abs_i32(int32_t value) { return value < 0 ? -value : value; }

/* This is deliberately the same permutation decision as vfc_select_context,
 * but with deterministic boundary ownership. The six permutations are the
 * six 60-degree SVPWM sectors in the phase-order coordinate system used by
 * the firmware. The zero-sequence-free plane is required; otherwise phase
 * ordering alone does not describe the commanded space vector. */
static bool geometry_sector_contains(uint8_t sector, int16_t mu, int16_t mv, int16_t mw)
{
    if ((int32_t)mu + (int32_t)mv + (int32_t)mw != 0) return false;
    if (mu == 0 && mv == 0 && mw == 0) return false;

    switch (sector) {
    case 0u: return mu >= mv && mv >= mw;
    case 1u: return mu >= mw && mw > mv;
    case 2u: return mv > mu && mu >= mw;
    case 3u: return mv >= mw && mw > mu;
    case 4u: return mw > mu && mu >= mv;
    case 5u: return mw >= mv && mv > mu;
    default: return false;
    }
}

static int32_t geometry_modulus(int16_t mu, int16_t mv, int16_t mw)
{
    int32_t a = abs_i32((int32_t)mu);
    int32_t b = abs_i32((int32_t)mv);
    int32_t c = abs_i32((int32_t)mw);
    return a > b ? (a > c ? a : c) : (b > c ? b : c);
}

static bool geometry_window_bounds(uint8_t window, const MapRegionQualification *q,
                                   int16_t *min_mod, int16_t *max_mod)
{
    int16_t lo;
    int16_t hi;

    if (q == 0 || min_mod == 0 || max_mod == 0 || window > 1u) return false;
    if (window == 0u) {
        lo = q->geometry_window0_min_mod_q15 ?
             q->geometry_window0_min_mod_q15 : GEOMETRY_DEFAULT_W0_MIN;
        hi = q->geometry_window0_max_mod_q15 ?
             q->geometry_window0_max_mod_q15 : GEOMETRY_DEFAULT_W0_MAX;
    } else {
        lo = q->geometry_window1_min_mod_q15 ?
             q->geometry_window1_min_mod_q15 : GEOMETRY_DEFAULT_W1_MIN;
        hi = q->geometry_window1_max_mod_q15 ?
             q->geometry_window1_max_mod_q15 : GEOMETRY_DEFAULT_W1_MAX;
    }
    if (lo <= 0 || hi <= lo) return false;
    *min_mod = lo;
    *max_mod = hi;
    return true;
}

static bool geometry_bounds_for_sector_window(uint8_t sector, uint8_t window,
                                               const MapRegionQualification *q,
                                               OewPwmRegion *out)
{
    int16_t mod_min;
    int16_t mod_max;

    if (!geometry_window_bounds(window, q, &mod_min, &mod_max) || out == 0 || sector > 5u) {
        return false;
    }

    memset(out, 0, sizeof(*out));
    /* Geometry uses the full Q15 phase cube as the storage envelope. The
     * actual region is the intersection of this envelope, the sector
     * predicate and the radial modulation interval. This avoids incorrectly
     * excluding legitimate 60-degree-edge vectors whose zero/other phase is
     * not at the radial extrema. */
    out->mu_min = INT16_MIN;
    out->mu_max = INT16_MAX;
    out->mv_min = INT16_MIN;
    out->mv_max = INT16_MAX;
    out->mw_min = INT16_MIN;
    out->mw_max = INT16_MAX;
    out->geometry_mod_min_q15 = mod_min;
    out->geometry_mod_max_q15 = mod_max;
    out->geometry_mode = 1u;
    return true;
}

static bool inside_statistical(const OewPwmRegion *r, const MapGridCell *c)
{
    return c->mu >= r->mu_min && c->mu <= r->mu_max &&
           c->mv >= r->mv_min && c->mv <= r->mv_max &&
           c->mw >= r->mw_min && c->mw <= r->mw_max;
}

static bool inside_geometry(const OewPwmRegion *r, uint8_t sector, uint8_t window,
                            const MapGridCell *c)
{
    int32_t mod;

    if (r == 0 || c == 0 || r->geometry_mode != 1u ||
        !geometry_sector_contains(sector, c->mu, c->mv, c->mw)) return false;
    mod = geometry_modulus(c->mu, c->mv, c->mw);
    if (mod < r->geometry_mod_min_q15) return false;
    /* Window 0 owns the common upper boundary exclusively. Window 1 owns its
     * lower boundary. Thus mod=10000 is selected by exactly one window when
     * the approved intervals are 6000..10000 and 10000..14000. */
    return window == 0u ? mod < r->geometry_mod_max_q15
                        : mod <= r->geometry_mod_max_q15;
}

static MapCertStatus certify_impl(const MapGridCell *cells, uint16_t cell_count,
                                  uint8_t sector, uint8_t window,
                                  const MapRegionQualification *q,
                                  OewPwmRegion *out, MapRegionReport *report,
                                  bool geometry)
{
    int16_t mu_min = INT16_MAX, mv_min = INT16_MAX, mw_min = INT16_MAX;
    int16_t mu_max = INT16_MIN, mv_max = INT16_MIN, mw_max = INT16_MIN;
    uint16_t valid = 0u, invalid = 0u, untested = 0u;
    uint16_t min_margin = UINT16_MAX;
    uint16_t i;

    if (report != 0) memset(report, 0, sizeof(*report));
    if (cells == 0 || q == 0 || out == 0 || report == 0 || cell_count == 0u ||
        cell_count > MAP_CERT_MAX_CELLS || q->min_valid_cells == 0u ||
        q->min_margin_ticks == 0u || q->guard_q15 < 0 || window > 1u || sector > 5u)
        return MAP_CERT_BAD_ARGUMENT;

    for (i = 0u; i < cell_count; ++i) {
        if (cells[i].status == 1u) {
            ++valid;
            mu_min = min_i16(mu_min, cells[i].mu);
            mv_min = min_i16(mv_min, cells[i].mv);
            mw_min = min_i16(mw_min, cells[i].mw);
            mu_max = max_i16(mu_max, cells[i].mu);
            mv_max = max_i16(mv_max, cells[i].mv);
            mw_max = max_i16(mw_max, cells[i].mw);
            if (cells[i].margin_ticks < min_margin) min_margin = cells[i].margin_ticks;
        } else if (cells[i].status == 2u) {
            ++invalid;
        } else {
            ++untested;
        }
    }
    report->valid_cells = valid;
    report->invalid_cells = invalid;
    report->untested_cells = untested;
    report->min_margin_ticks = min_margin == UINT16_MAX ? 0u : min_margin;
    if (valid < q->min_valid_cells) return MAP_CERT_NOT_ENOUGH_VALID;
    if (min_margin < q->min_margin_ticks) return MAP_CERT_MARGIN_BAD;

    if (geometry) {
        if (!geometry_bounds_for_sector_window(sector, window, q, out)) {
            return MAP_CERT_BAD_ARGUMENT;
        }
    } else {
        if (mu_max - mu_min < 2 * q->guard_q15 ||
            mv_max - mv_min < 2 * q->guard_q15 ||
            mw_max - mw_min < 2 * q->guard_q15) {
            return MAP_CERT_DEGENERATE;
        }
        memset(out, 0, sizeof(*out));
        out->mu_min = (int16_t)(mu_min + q->guard_q15);
        out->mu_max = (int16_t)(mu_max - q->guard_q15);
        out->mv_min = (int16_t)(mv_min + q->guard_q15);
        out->mv_max = (int16_t)(mv_max - q->guard_q15);
        out->mw_min = (int16_t)(mw_min + q->guard_q15);
        out->mw_max = (int16_t)(mw_max - q->guard_q15);
        out->geometry_mod_min_q15 = 0;
        out->geometry_mod_max_q15 = 0;
        out->geometry_mode = 0u;
    }
    out->min_margin_ticks = min_margin;
    out->valid = 1u;

    for (i = 0u; i < cell_count; ++i) {
        bool in = geometry ? inside_geometry(out, sector, window, &cells[i])
                           : inside_statistical(out, &cells[i]);
        if (geometry && cells[i].status == 1u && !in) return MAP_CERT_VALID_OUTSIDE;
        if (in) {
            if (cells[i].status == 2u) return MAP_CERT_INVALID_INSIDE;
            if (cells[i].status == 0u) return MAP_CERT_UNTESTED_INSIDE;
        }
    }
    report->ready = 1u;
    return MAP_CERT_OK;
}

MapCertStatus MapRegionCertify(const MapGridCell *cells, uint16_t cell_count,
                               const MapRegionQualification *q, OewPwmRegion *out,
                               MapRegionReport *report)
{
    return certify_impl(cells, cell_count, 0u, 0u, q, out, report, false);
}

MapCertStatus MapRegionCertifyForSectorWindow(const MapGridCell *cells, uint16_t cell_count,
                                               uint8_t sector, uint8_t window,
                                               const MapRegionQualification *q,
                                               OewPwmRegion *out, MapRegionReport *report)
{
    return certify_impl(cells, cell_count, sector, window, q, out, report,
                        q != 0 && q->use_geometry_bounds);
}
