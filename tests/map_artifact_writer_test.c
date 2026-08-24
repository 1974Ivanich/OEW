#include <assert.h>
#include <stdint.h>
#include <string.h>

#include "map_artifact_writer.h"

static void expect_u16(const uint8_t *p, uint16_t v)
{
    assert(p[0] == (uint8_t)v);
    assert(p[1] == (uint8_t)(v >> 8));
}

static void expect_u32(const uint8_t *p, uint32_t v)
{
    assert(p[0] == (uint8_t)v);
    assert(p[1] == (uint8_t)(v >> 8));
    assert(p[2] == (uint8_t)(v >> 16));
    assert(p[3] == (uint8_t)(v >> 24));
}

int main(void)
{
    OewMapIdentity identity = {
        7u, 20000u, 8499u, 0x4F455731u, 17u, 85u,
        42500000u, 1281u, 0u, 0x11223344u, 0x55667788u
    };
    OewMapProvenance provenance = {
        0x01020304u, 0xA1B2C3D4u, 0x10203040u,
        2u, 4u, 3u
    };
    CurrentReconEntry recon[OEW_CURRENT_MAP_SECTOR_COUNT][OEW_CURRENT_MAP_WINDOW_COUNT];
    OewPwmRegion regions[OEW_CURRENT_MAP_SECTOR_COUNT][OEW_CURRENT_MAP_WINDOW_COUNT];
    OewCurrentMap map;
    uint8_t wire[OEW_CURRENT_MAP_WIRE_SIZE];
    uint8_t too_small[OEW_CURRENT_MAP_WIRE_SIZE - 1u];
    size_t n;
    uint8_t *p;

    memset(recon, 0, sizeof(recon));
    memset(regions, 0, sizeof(regions));

    for (uint8_t s = 0u; s < OEW_CURRENT_MAP_SECTOR_COUNT; ++s) {
        for (uint8_t w = 0u; w < OEW_CURRENT_MAP_WINDOW_COUNT; ++w) {
            recon[s][w].valid = true;
            recon[s][w].phase_a = 0u;
            recon[s][w].phase_b = 1u;
            recon[s][w].m00 = 1000;
            recon[s][w].m01 = 0;
            recon[s][w].m10 = 0;
            recon[s][w].m11 = 1000;

            regions[s][w].mu_min = (int16_t)(-30000 + s * 1000 + w * 100);
            regions[s][w].mu_max = (int16_t)(-29000 + s * 1000 + w * 100);
            regions[s][w].mv_min = -20000;
            regions[s][w].mv_max = -19000;
            regions[s][w].mw_min = -10000;
            regions[s][w].mw_max = -9000;
            regions[s][w].min_margin_ticks = 37u;
            regions[s][w].valid = 1u;
        }
    }

    assert(MapArtifactWriter_Build(&identity, &provenance,
                                   2u, 1u, 25u,
                                   -1234, 2345, -3456,
                                   recon, regions, &map));

    memset(wire, 0xA5, sizeof(wire));
    n = MapArtifactWriter_EncodeBinary(&map, wire, sizeof(wire));
    assert(n == OEW_CURRENT_MAP_WIRE_SIZE);
    assert(MapArtifactWriter_EncodeBinary(&map, too_small, sizeof(too_small)) == 0u);

    p = wire;
    expect_u32(p, OEW_CURRENT_MAP_MAGIC); p += 4;
    expect_u16(p, OEW_CURRENT_MAP_REVISION); p += 2;
    expect_u16(p, 7u); p += 2;
    expect_u32(p, 20000u); p += 4;
    expect_u32(p, 8499u); p += 4;
    expect_u32(p, 0x4F455731u); p += 4;
    expect_u16(p, 17u); p += 2;
    expect_u16(p, 85u); p += 2;
    expect_u32(p, 42500000u); p += 4;
    expect_u16(p, 1281u); p += 2;
    assert(*p++ == 0u); /* adc_resolution: 12-bit */
    expect_u32(p, 0x11223344u); p += 4;
    expect_u32(p, 0x55667788u); p += 4;

    expect_u32(p, 0x01020304u); p += 4;
    expect_u32(p, 0xA1B2C3D4u); p += 4;
    expect_u32(p, 0x10203040u); p += 4;
    expect_u32(p, 2u); p += 4;
    expect_u32(p, 4u); p += 4;
    expect_u32(p, 3u); p += 4;

    assert(*p++ == 2u);
    assert(*p++ == 1u);
    expect_u16(p, 25u); p += 2;
    expect_u16(p, (uint16_t)-1234); p += 2;
    expect_u16(p, (uint16_t)2345); p += 2;
    expect_u16(p, (uint16_t)-3456); p += 2;

    /* First reconstruction entry: valid, U/V, identity matrix. */
    assert(*p++ == 1u);
    assert(*p++ == 0u);
    assert(*p++ == 1u);
    expect_u32(p, 1000u); p += 4;
    expect_u32(p, 0u); p += 4;
    expect_u32(p, 0u); p += 4;
    expect_u32(p, 1000u); p += 4;

    /* Skip the remaining 11 canonical 19-byte reconstruction entries. */
    p += 11u * 19u;
    assert((size_t)(p - wire) == 301u);

    /* First region entry. */
    expect_u16(p, (uint16_t)-30000); p += 2;
    expect_u16(p, (uint16_t)-29000); p += 2;
    expect_u16(p, (uint16_t)-20000); p += 2;
    expect_u16(p, (uint16_t)-19000); p += 2;
    expect_u16(p, (uint16_t)-10000); p += 2;
    expect_u16(p, (uint16_t)-9000); p += 2;
    expect_u16(p, 37u); p += 2;
    assert(*p++ == 1u);
    assert(*p++ == 0u);

    p = wire + OEW_CURRENT_MAP_WIRE_SIZE - 4u;
    expect_u32(p, map.crc32);

    /* The canonical encoder must not depend on host struct padding. */
    assert(n != sizeof(map));

    /* Tampering with any map field invalidates the source CRC and therefore
     * encoding must fail rather than emitting an uncertified artifact. */
    map.deadtime_ticks++;
    assert(MapArtifactWriter_EncodeBinary(&map, wire, sizeof(wire)) == 0u);

    return 0;
}
