#include "map_region_certifier.h"

#include <limits.h>
#include <string.h>

static int16_t min_i16(int16_t a, int16_t b) { return a < b ? a : b; }
static int16_t max_i16(int16_t a, int16_t b) { return a > b ? a : b; }

static bool inside(const OewPwmRegion *r, const MapGridCell *c)
{
    return c->mu >= r->mu_min && c->mu <= r->mu_max &&
           c->mv >= r->mv_min && c->mv <= r->mv_max &&
           c->mw >= r->mw_min && c->mw <= r->mw_max;
}

MapCertStatus MapRegionCertify(
    const MapGridCell *cells,
    uint16_t cell_count,
    const MapRegionQualification *qualification,
    OewPwmRegion *out,
    MapRegionReport *report)
{
    int16_t mu_min = INT16_MAX, mv_min = INT16_MAX, mw_min = INT16_MAX;
    int16_t mu_max = INT16_MIN, mv_max = INT16_MIN, mw_max = INT16_MIN;
    uint16_t valid = 0u, invalid = 0u, untested = 0u;
    uint16_t min_margin = UINT16_MAX;
    uint16_t i;
    if (report != 0) memset(report, 0, sizeof(*report));
    if (cells == 0 || qualification == 0 || out == 0 || report == 0 ||
        cell_count == 0u || cell_count > MAP_CERT_MAX_CELLS ||
        qualification->min_valid_cells == 0u ||
        qualification->min_margin_ticks == 0u || qualification->guard_q15 < 0) {
        return MAP_CERT_BAD_ARGUMENT;
    }
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
    if (valid < qualification->min_valid_cells) return MAP_CERT_NOT_ENOUGH_VALID;
    if (min_margin < qualification->min_margin_ticks) return MAP_CERT_MARGIN_BAD;
    if (mu_max - mu_min < 2 * qualification->guard_q15 ||
        mv_max - mv_min < 2 * qualification->guard_q15 ||
        mw_max - mw_min < 2 * qualification->guard_q15) {
        return MAP_CERT_DEGENERATE;
    }
    memset(out, 0, sizeof(*out));
    out->mu_min = (int16_t)(mu_min + qualification->guard_q15);
    out->mu_max = (int16_t)(mu_max - qualification->guard_q15);
    out->mv_min = (int16_t)(mv_min + qualification->guard_q15);
    out->mv_max = (int16_t)(mv_max - qualification->guard_q15);
    out->mw_min = (int16_t)(mw_min + qualification->guard_q15);
    out->mw_max = (int16_t)(mw_max - qualification->guard_q15);
    out->min_margin_ticks = min_margin;
    out->valid = 1u;
    for (i = 0u; i < cell_count; ++i) {
        if (inside(out, &cells[i])) {
            if (cells[i].status == 2u) return MAP_CERT_INVALID_INSIDE;
            if (cells[i].status == 0u) return MAP_CERT_UNTESTED_INSIDE;
        }
    }
    report->ready = 1u;
    return MAP_CERT_OK;
}
