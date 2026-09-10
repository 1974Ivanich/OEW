#include "map_region_certifier.h"

#include <limits.h>
#include <string.h>

#define GEOMETRY_DEFAULT_W0_MIN 6000
#define GEOMETRY_DEFAULT_W0_MAX 10000
#define GEOMETRY_DEFAULT_W1_MIN 10000
#define GEOMETRY_DEFAULT_W1_MAX 14000

static int16_t min_i16(int16_t a, int16_t b) { return a < b ? a : b; }
static int16_t max_i16(int16_t a, int16_t b) { return a > b ? a : b; }

static bool inside(const OewPwmRegion *r, const MapGridCell *c)
{
    return c->mu >= r->mu_min && c->mu <= r->mu_max &&
           c->mv >= r->mv_min && c->mv <= r->mv_max &&
           c->mw >= r->mw_min && c->mw <= r->mw_max;
}

static bool geometry_bounds_for_sector_window(uint8_t sector, uint8_t window,
                                               const MapRegionQualification *q,
                                               OewPwmRegion *out)
{
    int16_t mod_min, mod_max;
    if (window == 0u) {
        mod_min = q->geometry_window0_min_mod_q15 ? q->geometry_window0_min_mod_q15 : GEOMETRY_DEFAULT_W0_MIN;
        mod_max = q->geometry_window0_max_mod_q15 ? q->geometry_window0_max_mod_q15 : GEOMETRY_DEFAULT_W0_MAX;
    } else {
        mod_min = q->geometry_window1_min_mod_q15 ? q->geometry_window1_min_mod_q15 : GEOMETRY_DEFAULT_W1_MIN;
        mod_max = q->geometry_window1_max_mod_q15 ? q->geometry_window1_max_mod_q15 : GEOMETRY_DEFAULT_W1_MAX;
    }
    if (mod_min <= 0 || mod_max <= mod_min) return false;

    memset(out, 0, sizeof(*out));
    switch (sector) {
    case 0u: /* mu > 0 > mv > mw */
        out->mu_min = mod_min;  out->mu_max = mod_max;
        out->mv_min = -mod_max; out->mv_max = -1;
        out->mw_min = -mod_max; out->mw_max = (int16_t)-mod_min;
        break;
    case 1u: /* mu > mw > mv */
        out->mu_min = mod_min;  out->mu_max = mod_max;
        out->mw_min = -mod_max; out->mw_max = -1;
        out->mv_min = -mod_max; out->mv_max = (int16_t)-mod_min;
        break;
    case 2u: /* mv > 0 > mu > mw */
        out->mv_min = mod_min;  out->mv_max = mod_max;
        out->mu_min = -mod_max; out->mu_max = -1;
        out->mw_min = -mod_max; out->mw_max = (int16_t)-mod_min;
        break;
    case 3u: /* mv > mw > mu */
        out->mv_min = mod_min;  out->mv_max = mod_max;
        out->mw_min = -mod_max; out->mw_max = -1;
        out->mu_min = -mod_max; out->mu_max = (int16_t)-mod_min;
        break;
    case 4u: /* mw > 0 > mv > mu */
        out->mw_min = mod_min;  out->mw_max = mod_max;
        out->mv_min = -mod_max; out->mv_max = -1;
        out->mu_min = -mod_max; out->mu_max = (int16_t)-mod_min;
        break;
    case 5u: /* mw > mu > mv */
        out->mw_min = mod_min;  out->mw_max = mod_max;
        out->mu_min = -mod_max; out->mu_max = -1;
        out->mv_min = -mod_max; out->mv_max = (int16_t)-mod_min;
        break;
    default:
        return false;
    }
    return true;
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
            mu_min = min_i16(mu_min, cells[i].mu); mv_min = min_i16(mv_min, cells[i].mv); mw_min = min_i16(mw_min, cells[i].mw);
            mu_max = max_i16(mu_max, cells[i].mu); mv_max = max_i16(mv_max, cells[i].mv); mw_max = max_i16(mw_max, cells[i].mw);
            if (cells[i].margin_ticks < min_margin) min_margin = cells[i].margin_ticks;
        } else if (cells[i].status == 2u) ++invalid;
        else ++untested;
    }
    report->valid_cells = valid; report->invalid_cells = invalid; report->untested_cells = untested;
    report->min_margin_ticks = min_margin == UINT16_MAX ? 0u : min_margin;
    if (valid < q->min_valid_cells) return MAP_CERT_NOT_ENOUGH_VALID;
    if (min_margin < q->min_margin_ticks) return MAP_CERT_MARGIN_BAD;

    if (geometry) {
        if (!geometry_bounds_for_sector_window(sector, window, q, out)) return MAP_CERT_BAD_ARGUMENT;
    } else {
        if (mu_max - mu_min < 2 * q->guard_q15 || mv_max - mv_min < 2 * q->guard_q15 || mw_max - mw_min < 2 * q->guard_q15)
            return MAP_CERT_DEGENERATE;
        memset(out, 0, sizeof(*out));
        out->mu_min = (int16_t)(mu_min + q->guard_q15); out->mu_max = (int16_t)(mu_max - q->guard_q15);
        out->mv_min = (int16_t)(mv_min + q->guard_q15); out->mv_max = (int16_t)(mv_max - q->guard_q15);
        out->mw_min = (int16_t)(mw_min + q->guard_q15); out->mw_max = (int16_t)(mw_max - q->guard_q15);
    }
    out->min_margin_ticks = min_margin; out->valid = 1u;

    for (i = 0u; i < cell_count; ++i) {
        if (geometry && cells[i].status == 1u && !inside(out, &cells[i])) return MAP_CERT_VALID_OUTSIDE;
        if (inside(out, &cells[i])) {
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
