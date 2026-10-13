#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "map_wire_protocol.h"

static void fill_sample(MapWireSample *s)
{
    memset(s, 0, sizeof(*s));
    s->sample_id = 0x10203040u;
    s->capture_id = 0x55667788u;
    s->sector = 4u;
    s->window = 1u;
    s->tim1_ccr[0] = 375u; s->tim1_ccr[1] = 501u; s->tim1_ccr[2] = 623u;
    s->tim8_ccr[0] = 501u; s->tim8_ccr[1] = 623u; s->tim8_ccr[2] = 375u;
    s->tim1_arr = 999u;
    s->trigger_offset_ticks = 543u;
    s->deadtime_ticks = 192u;
    s->pwm_frequency_hz = 5000u;
    s->trigger_revision = 0x4F455732u;
    s->phase_u_ma = -1234;
    s->phase_v_ma = 5678;
    s->phase_w_ma = -4444;
    s->reference_valid = 1u;
    s->reference_source = 2u;
    s->margin_ticks = 110u;
    s->blanking_ticks = 32u;
    s->adc_settled = 1u;
    s->scope_qualified = 1u;
    s->reference_timestamp_cycles = 0xAABBCCDDu;
}

static MapWireEvent feed(MapWireParser *p, const uint8_t *buf, size_t len,
                         MapWireSample *out)
{
    MapWireEvent event = MAP_WIRE_EVENT_NONE;
    size_t i;
    for (i = 0u; i < len; ++i) {
        MapWireEvent current = MapWireParser_PushByte(p, buf[i], out);
        if (current != MAP_WIRE_EVENT_NONE) event = current;
    }
    return event;
}

int main(void)
{
    MapWireSample expected, actual;
    MapWireParser parser;
    uint8_t frame[MAP_WIRE_MAX_ENCODED];
    size_t frame_len;

    fill_sample(&expected);
    assert(MapWire_SampleWireSize() == 64u);
    assert(MapWire_EncodeSample(7u, 9u, &expected, frame, sizeof(frame), &frame_len));
    assert(frame_len > 1u && frame[frame_len - 1u] == 0u);

    MapWireParser_Init(&parser);
    memset(&actual, 0, sizeof(actual));
    assert(feed(&parser, frame, frame_len / 2u, &actual) == MAP_WIRE_EVENT_NONE);
    assert(feed(&parser, frame + frame_len / 2u,
                frame_len - frame_len / 2u, &actual) == MAP_WIRE_EVENT_SAMPLE);
    assert(memcmp(&expected, &actual, sizeof(expected)) == 0);
    assert(parser.session_id == 7u);
    assert(parser.transport_sequence == 9u);

    /* Corrupt one encoded byte; CRC or COBS validation must reject it. */
    frame[frame_len / 3u] ^= 0x01u;
    MapWireParser_Init(&parser);
    assert(feed(&parser, frame, frame_len, &actual) != MAP_WIRE_EVENT_SAMPLE);
    assert(parser.format_errors + parser.overflow_errors >= 1u);

    /* A frame without a delimiter cannot leak a sample. */
    MapWireParser_Init(&parser);
    assert(feed(&parser, frame, frame_len - 1u, &actual) != MAP_WIRE_EVENT_SAMPLE);

    /* An oversized unterminated frame is rejected and parser recovers. */
    MapWireParser_Init(&parser);
    {
        size_t i;
        for (i = 0u; i < MAP_WIRE_MAX_DECODED + 1u; ++i) {
            (void)MapWireParser_PushByte(&parser, 0x55u, &actual);
        }
    }
    assert(parser.overflow_errors == 1u);

    puts("map_wire_protocol_test: PASS");
    return 0;
}
