#include "map_wire_protocol.h"

#include <string.h>

#define MAP_WIRE_HEADER_SIZE 20u
#define MAP_WIRE_SAMPLE_SIZE 64u
#define MAP_WIRE_CRC_SIZE     4u
#define MAP_WIRE_TOTAL_SIZE  (MAP_WIRE_HEADER_SIZE + MAP_WIRE_SAMPLE_SIZE + MAP_WIRE_CRC_SIZE)

static void put16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
}

static void put32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static uint16_t get16(const uint8_t *p)
{
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static uint32_t get32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

uint32_t MapWire_Crc32(const uint8_t *data, size_t length)
{
    uint32_t crc = 0xFFFFFFFFu;
    size_t i;
    unsigned bit;

    if (data == 0 && length != 0u) return 0u;
    for (i = 0u; i < length; ++i) {
        crc ^= data[i];
        for (bit = 0u; bit < 8u; ++bit) {
            crc = (crc >> 1) ^
                  (0xEDB88320u & (uint32_t)-(int32_t)(crc & 1u));
        }
    }
    return ~crc;
}

size_t MapWire_SampleWireSize(void)
{
    return MAP_WIRE_SAMPLE_SIZE;
}

static void encode_sample_body(uint8_t *p, const MapWireSample *s)
{
    size_t i;
    put32(p + 0u, s->sample_id);
    put32(p + 4u, s->capture_id);
    p[8] = s->sector;
    p[9] = s->window;
    put16(p + 10u, 0u);
    for (i = 0u; i < 3u; ++i) put16(p + 12u + i * 2u, s->tim1_ccr[i]);
    for (i = 0u; i < 3u; ++i) put16(p + 18u + i * 2u, s->tim8_ccr[i]);
    put16(p + 24u, s->tim1_arr);
    put16(p + 26u, s->trigger_offset_ticks);
    put16(p + 28u, s->deadtime_ticks);
    put32(p + 30u, s->pwm_frequency_hz);
    put32(p + 34u, s->trigger_revision);
    put32(p + 38u, (uint32_t)s->phase_u_ma);
    put32(p + 42u, (uint32_t)s->phase_v_ma);
    put32(p + 46u, (uint32_t)s->phase_w_ma);
    p[50] = s->reference_valid;
    p[51] = s->reference_source;
    put16(p + 52u, s->margin_ticks);
    put16(p + 54u, s->blanking_ticks);
    p[56] = s->adc_settled;
    p[57] = s->scope_qualified;
    put16(p + 58u, 0u);
    put32(p + 60u, s->reference_timestamp_cycles);
}

static bool decode_sample_body(const uint8_t *p, MapWireSample *s)
{
    size_t i;
    if (p == 0 || s == 0) return false;
    if (get16(p + 10u) != 0u || get16(p + 58u) != 0u) return false;

    memset(s, 0, sizeof(*s));
    s->sample_id = get32(p + 0u);
    s->capture_id = get32(p + 4u);
    s->sector = p[8];
    s->window = p[9];
    for (i = 0u; i < 3u; ++i) s->tim1_ccr[i] = get16(p + 12u + i * 2u);
    for (i = 0u; i < 3u; ++i) s->tim8_ccr[i] = get16(p + 18u + i * 2u);
    s->tim1_arr = get16(p + 24u);
    s->trigger_offset_ticks = get16(p + 26u);
    s->deadtime_ticks = get16(p + 28u);
    s->pwm_frequency_hz = get32(p + 30u);
    s->trigger_revision = get32(p + 34u);
    s->phase_u_ma = (int32_t)get32(p + 38u);
    s->phase_v_ma = (int32_t)get32(p + 42u);
    s->phase_w_ma = (int32_t)get32(p + 46u);
    s->reference_valid = p[50];
    s->reference_source = p[51];
    s->margin_ticks = get16(p + 52u);
    s->blanking_ticks = get16(p + 54u);
    s->adc_settled = p[56];
    s->scope_qualified = p[57];
    s->reference_timestamp_cycles = get32(p + 60u);
    return true;
}

/* Encode one decoded packet using COBS. Zero is reserved as the frame delimiter. */
static size_t cobs_encode(const uint8_t *src, size_t len,
                          uint8_t *dst, size_t capacity)
{
    size_t read = 0u;
    size_t write = 1u;
    size_t code_index = 0u;
    uint8_t code = 1u;

    if (src == 0 || dst == 0 || capacity == 0u) return 0u;
    while (read < len) {
        if (src[read] == 0u) {
            if (code_index >= capacity) return 0u;
            dst[code_index] = code;
            code_index = write++;
            code = 1u;
            ++read;
        } else {
            if (write >= capacity) return 0u;
            dst[write++] = src[read++];
            ++code;
            if (code == 0xFFu) {
                dst[code_index] = code;
                code_index = write++;
                code = 1u;
            }
        }
    }
    if (code_index >= capacity || write >= capacity) return 0u;
    dst[code_index] = code;
    return write;
}

static size_t cobs_decode(uint8_t *buf, size_t len)
{
    size_t read = 0u;
    size_t write = 0u;

    if (buf == 0 || len == 0u) return 0u;
    while (read < len) {
        uint8_t code = buf[read++];
        size_t i;
        if (code == 0u || read + (size_t)code - 1u > len) return 0u;
        for (i = 1u; i < code; ++i) buf[write++] = buf[read++];
        if (code != 0xFFu && read < len) buf[write++] = 0u;
    }
    return write;
}

static bool parse_decoded(const uint8_t *raw, size_t len, MapWireSample *out)
{
    uint16_t payload_len;
    uint32_t expected_crc;

    if (raw == 0 || out == 0 || len != MAP_WIRE_TOTAL_SIZE) return false;
    if (get16(raw + 0u) != MAP_WIRE_MAGIC || raw[2] != MAP_WIRE_VERSION ||
        raw[3] != MAP_WIRE_MSG_SAMPLE || get16(raw + 4u) != MAP_WIRE_HEADER_SIZE) {
        return false;
    }
    payload_len = get16(raw + 6u);
    if (payload_len != MAP_WIRE_SAMPLE_SIZE) return false;
    expected_crc = get32(raw + MAP_WIRE_HEADER_SIZE + MAP_WIRE_SAMPLE_SIZE);
    if (MapWire_Crc32(raw, MAP_WIRE_HEADER_SIZE + MAP_WIRE_SAMPLE_SIZE) != expected_crc) {
        return false;
    }
    return decode_sample_body(raw + MAP_WIRE_HEADER_SIZE, out);
}

void MapWireParser_Init(MapWireParser *parser)
{
    if (parser != 0) memset(parser, 0, sizeof(*parser));
}

MapWireEvent MapWireParser_PushByte(MapWireParser *parser,
                                    uint8_t byte,
                                    MapWireSample *sample_out)
{
    uint8_t raw[MAP_WIRE_MAX_DECODED];
    size_t raw_len;
    MapWireEvent event;

    if (parser == 0) return MAP_WIRE_EVENT_FORMAT_ERROR;
    if (byte != 0u) {
        if (parser->decoded_len >= sizeof(parser->decoded)) {
            parser->decoded_len = 0u;
            parser->frame_open = 0u;
            ++parser->overflow_errors;
            return MAP_WIRE_EVENT_OVERFLOW;
        }
        parser->decoded[parser->decoded_len++] = byte;
        parser->frame_open = 1u;
        return MAP_WIRE_EVENT_NONE;
    }
    if (parser->decoded_len == 0u) return MAP_WIRE_EVENT_NONE;

    memcpy(raw, parser->decoded, parser->decoded_len);
    raw_len = cobs_decode(raw, parser->decoded_len);
    parser->decoded_len = 0u;
    parser->frame_open = 0u;
    if (raw_len == 0u) {
        ++parser->format_errors;
        return MAP_WIRE_EVENT_FORMAT_ERROR;
    }
    event = parse_decoded(raw, raw_len, sample_out)
              ? MAP_WIRE_EVENT_SAMPLE : MAP_WIRE_EVENT_FORMAT_ERROR;
    if (event == MAP_WIRE_EVENT_SAMPLE) {
        parser->session_id = get32(raw + 8u);
        parser->transport_sequence = get32(raw + 12u);
    }
    if (event == MAP_WIRE_EVENT_FORMAT_ERROR) ++parser->format_errors;
    return event;
}

bool MapWire_EncodeSample(uint32_t session_id,
                          uint32_t sequence,
                          const MapWireSample *sample,
                          uint8_t *out,
                          size_t out_capacity,
                          size_t *out_length)
{
    uint8_t raw[MAP_WIRE_TOTAL_SIZE];
    size_t encoded_len;
    size_t i;
    uint32_t crc;

    if (sample == 0 || out == 0 || out_length == 0 || out_capacity < 2u) {
        return false;
    }
    memset(raw, 0, sizeof(raw));
    put16(raw + 0u, MAP_WIRE_MAGIC);
    raw[2] = MAP_WIRE_VERSION;
    raw[3] = MAP_WIRE_MSG_SAMPLE;
    put16(raw + 4u, MAP_WIRE_HEADER_SIZE);
    put16(raw + 6u, MAP_WIRE_SAMPLE_SIZE);
    put32(raw + 8u, session_id);
    put32(raw + 12u, sequence);
    put32(raw + 16u, 0u);
    encode_sample_body(raw + MAP_WIRE_HEADER_SIZE, sample);
    crc = MapWire_Crc32(raw, MAP_WIRE_HEADER_SIZE + MAP_WIRE_SAMPLE_SIZE);
    put32(raw + MAP_WIRE_HEADER_SIZE + MAP_WIRE_SAMPLE_SIZE, crc);

    encoded_len = cobs_encode(raw, sizeof(raw), out, out_capacity - 1u);
    if (encoded_len == 0u || encoded_len + 1u > out_capacity) return false;
    for (i = encoded_len; i < out_capacity; ++i) out[i] = 0u;
    out[encoded_len] = 0u;
    *out_length = encoded_len + 1u;
    return true;
}
