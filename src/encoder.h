#ifndef ENCODER_H
#define ENCODER_H

#include <stdint.h>

/* Error flag bits returned by ENC_GetError() */
#define ENC_ERR_PARITY      0x01u   /* parity check failed */
#define ENC_ERR_EF          0x02u   /* AS5048A error flag (bit 14) */
#define ENC_ERR_TIMEOUT     0x04u   /* SPI busy-wait timeout */

void     ENC_Init(void);              /* SPI2 + CS pin (PB6) */
uint16_t ENC_ReadRaw(void);           /* raw 16-bit frame from AS5048A */
uint16_t ENC_GetAngle14(void);        /* 0..16383 (14-bit, after parity check) */
int32_t  ENC_GetSpeed_rpm(void);     /* mechanical speed, rpm (signed) */
int32_t  ENC_GetAngle_deg(void);     /* 0..360 */
void     ENC_Update(void);           /* TIM6 ISR (1 kHz): SPI read + speed calc */
uint8_t  ENC_GetError(void);         /* error flag (parity, EF) */

#endif
