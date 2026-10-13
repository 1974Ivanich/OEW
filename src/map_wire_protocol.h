#ifndef MAP_WIRE_PROTOCOL_H
#define MAP_WIRE_PROTOCOL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MAP_WIRE_MAGIC              0x4D50u /* "MP" */
#define MAP_WIRE_VERSION            1u
#define MAP_WIRE_MSG_SAMPLE         3u
#define MAP_WIRE_MAX_PAYLOAD        128u
#define MAP_WIRE_MAX_DECODED        192u
#define MAP_WIRE_MAX_ENCODED        (MAP_WIRE_MAX_DECODED + 2u + (MAP_WIRE_MAX_DECODED / 254u))

/* Wire values are little-endian; these types are logical fields, not overlays. */
typedef struct {
    uint32_t sample_id;
    uint32_t capture_id;
    uint8_t sector;
    uint8_t window;
    uint16_t tim1_ccr[3];
    uint16_t tim8_ccr[3];
    uint16_t tim1_arr;
    uint16_t trigger_offset_ticks;
    uint16_t deadtime_ticks;
    uint32_t pwm_frequency_hz;
    uint32_t trigger_revision;
    int32_t phase_u_ma;
    int32_t phase_v_ma;
    int32_t phase_w_ma;
    uint8_t reference_valid;
    uint8_t reference_source;
    uint16_t margin_ticks;
    uint16_t blanking_ticks;
    uint8_t adc_settled;
    uint8_t scope_qualified;
    uint32_t reference_timestamp_cycles;
} MapWireSample;

typedef enum {
    MAP_WIRE_EVENT_NONE = 0,
    MAP_WIRE_EVENT_SAMPLE = 1,
    MAP_WIRE_EVENT_CRC_ERROR = -1,
    MAP_WIRE_EVENT_FORMAT_ERROR = -2,
    MAP_WIRE_EVENT_OVERFLOW = -3,
    MAP_WIRE_EVENT_UNSUPPORTED = -4
} MapWireEvent;

typedef struct {
    uint8_t decoded[MAP_WIRE_MAX_DECODED];
    size_t decoded_len;
    uint8_t frame_open;
    uint32_t session_id;
    uint32_t transport_sequence;
    uint32_t crc_errors;
    uint32_t format_errors;
    uint32_t overflow_errors;
    uint32_t unsupported_errors;
} MapWireParser;

void MapWireParser_Init(MapWireParser *parser);
MapWireEvent MapWireParser_PushByte(MapWireParser *parser,
                                    uint8_t byte,
                                    MapWireSample *sample_out);

/* Build one COBS-framed SAMPLE packet, including the trailing 0 delimiter. */
bool MapWire_EncodeSample(uint32_t session_id,
                          uint32_t sequence,
                          const MapWireSample *sample,
                          uint8_t *out,
                          size_t out_capacity,
                          size_t *out_length);

uint32_t MapWire_Crc32(const uint8_t *data, size_t length);
size_t MapWire_SampleWireSize(void);

#endif /* MAP_WIRE_PROTOCOL_H */
