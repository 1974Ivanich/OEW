#include "stm32g474xx.h"
#include "encoder.h"

/* AS5048A SPI2 driver — CMSIS only, no HAL.
 * RM0440 section 28 (SPI), AS5048A datasheet (AMS DS000290).
 *
 * Pinout:
 *   PB6  — CS  (GPIO output, pull-up)
 *   PB10 — SCK (AF5, SPI2_SCK)
 *   PB14 — MISO (AF5, SPI2_MISO, pull-up)
 *   PB15 — MOSI (AF5, SPI2_MOSI)
 *
 * SPI: CPOL=1, CPHA=1, 16-bit frame, master.
 * APB1 = 170 MHz, BR=/32 → 5.3 MHz (< 10 MHz AS5048A limit).
 *
 * Protocol: 2-transaction read (reliable, re-sends command each cycle).
 *   TX1: read command (auto-parity via ENC_MakeReadCmd)
 *   TX2: 0x0000 (NOP) → RX2 = [par(1)|EF(1)|angle(14)] */

#define AS5048A_REG_ANGLE   0x3FFEu  /* ANGLE register (datasheet DS000290) */
#define AS5048A_NOP         0x0000u
#define AS5048A_EF_BIT      (1U << 14)

#define ENC_SPI_TIMEOUT     10000u
#define ENC_COUNTS_PER_REV  16384
#define ENC_FILTER_SHIFT    3       /* IIR 1/8 */

static volatile uint16_t enc_raw = 0;
static volatile uint16_t enc_angle14 = 0;
static volatile int32_t  enc_speed_rpm = 0;
static volatile uint8_t  enc_error = 0;
static volatile uint32_t enc_err_count = 0;
static volatile uint8_t  enc_first_read = 1;
static uint16_t prev_angle14 = 0;
static uint16_t enc_cmd_angle = 0;  /* pre-computed read command with parity */

/* ── SPI2 low-level ─────────────────────────────────────────────── */

static void spi2_cs_low(void)  { GPIOB->BSRR = (1U << (6 + 16)); }
static void spi2_cs_high(void) { GPIOB->BSRR = (1U << 6); }

/* Delay >= 350 ns between transactions (t_CSn per datasheet).
 * 170 MHz: 20 NOP iterations ~ 350 ns. */
static void spi2_delay_csn(void) {
    for(volatile uint32_t i = 0; i < 20; i++) __NOP();
}

static uint16_t spi2_xfer(uint16_t tx) {
    uint32_t t = ENC_SPI_TIMEOUT;
    while(!(SPI2->SR & SPI_SR_TXE)) { if(--t == 0) { enc_error = ENC_ERR_TIMEOUT; return 0xFFFF; } }
    *(volatile uint16_t *)&SPI2->DR = tx;
    t = ENC_SPI_TIMEOUT;
    while(!(SPI2->SR & SPI_SR_RXNE)) { if(--t == 0) { enc_error = ENC_ERR_TIMEOUT; return 0xFFFF; } }
    uint16_t rx = *(volatile uint16_t *)&SPI2->DR;
    t = ENC_SPI_TIMEOUT;
    while(SPI2->SR & SPI_SR_BSY) { if(--t == 0) break; }
    return rx;
}

/* ── Parity (XOR-tree, even parity over 16-bit frame) ───────────── */

static uint8_t parity_ok(uint16_t frame) {
    uint16_t x = frame;
    x ^= x >> 8; x ^= x >> 4; x ^= x >> 2; x ^= x >> 1;
    return (uint8_t)(!(x & 1));  /* even -> OK */
}

/* Build read command with auto-parity: bit14=1 (read), bits13:0=addr */
static uint16_t enc_make_read_cmd(uint16_t addr) {
    uint16_t f = 0x4000u | (addr & 0x3FFFu);
    uint16_t x = f & 0x7FFFu;
    x ^= x >> 8; x ^= x >> 4; x ^= x >> 2; x ^= x >> 1;
    if(x & 1u) f |= 0x8000u;  /* odd -> set parity bit */
    return f;
}

/* ── Public API ─────────────────────────────────────────────────── */

void ENC_Init(void) {
    RCC->AHB2ENR  |= RCC_AHB2ENR_GPIOBEN;
    RCC->APB1ENR1 |= RCC_APB1ENR1_SPI2EN;
    (void)RCC->APB1ENR1;  /* sync after clock enable */

    /* PB6 — CS (GPIO output, push-pull, pull-up) */
    GPIOB->MODER &= ~(3U << (6 * 2));
    GPIOB->MODER |=  (1U << (6 * 2));   /* output */
    GPIOB->OTYPER &= ~(1U << 6);       /* push-pull */
    GPIOB->PUPDR &= ~(3U << (6 * 2));
    GPIOB->PUPDR |=  (1U << (6 * 2));  /* pull-up */
    GPIOB->OSPEEDR |= (3U << (6 * 2)); /* high speed */
    spi2_cs_high();

    /* PB10 — SPI2_SCK (AF5) */
    GPIOB->MODER &= ~(3U << (10 * 2));
    GPIOB->MODER |=  (2U << (10 * 2)); /* AF */
    GPIOB->AFR[1] &= ~(0xF << ((10 - 8) * 4));
    GPIOB->AFR[1] |=  (5U << ((10 - 8) * 4));
    GPIOB->OSPEEDR |= (3U << (10 * 2));

    /* PB14 — SPI2_MISO (AF5, pull-up for floating line protection) */
    GPIOB->MODER &= ~(3U << (14 * 2));
    GPIOB->MODER |=  (2U << (14 * 2));
    GPIOB->AFR[1] &= ~(0xF << ((14 - 8) * 4));
    GPIOB->AFR[1] |=  (5U << ((14 - 8) * 4));
    GPIOB->OSPEEDR |= (3U << (14 * 2));
    GPIOB->PUPDR &= ~(3U << (14 * 2));
    GPIOB->PUPDR |=  (1U << (14 * 2));  /* pull-up */

    /* PB15 — SPI2_MOSI (AF5) */
    GPIOB->MODER &= ~(3U << (15 * 2));
    GPIOB->MODER |=  (2U << (15 * 2));
    GPIOB->AFR[1] &= ~(0xF << ((15 - 8) * 4));
    GPIOB->AFR[1] |=  (5U << ((15 - 8) * 4));
    GPIOB->OSPEEDR |= (3U << (15 * 2));

    /* SPI2 config: Master, CPOL=1, CPHA=1, BR=/32, SSM=1, SSI=1, 16-bit, MSB first.
     * STM32G4: no DFF bit — use CR2 DS[3:0]=0xF for 16-bit data size. */
    SPI2->CR1 = 0;  /* disable before config */
    SPI2->CR1 = SPI_CR1_MSTR
              | SPI_CR1_CPOL
              | SPI_CR1_CPHA
              | (4U << SPI_CR1_BR_Pos)  /* /32 = 170/32 = 5.3 MHz */
              | SPI_CR1_SSM
              | SPI_CR1_SSI;

    SPI2->CR2 = (0xFU << SPI_CR2_DS_Pos);  /* 16-bit, SSOE=0, FRXTH=0 */

    /* Flush RX FIFO before enable */
    while(SPI2->SR & SPI_SR_RXNE) { (void)SPI2->DR; }

    SPI2->CR1 |= SPI_CR1_SPE;  /* enable SPI2 */

    /* Pre-compute read command with auto-parity */
    enc_cmd_angle = enc_make_read_cmd(AS5048A_REG_ANGLE);

    /* Initial 2-transaction read to prime the encoder */
    spi2_cs_low();
    spi2_xfer(enc_cmd_angle);
    spi2_cs_high();
    spi2_delay_csn();
    spi2_cs_low();
    uint16_t r = spi2_xfer(AS5048A_NOP);
    spi2_cs_high();
    enc_raw = r;
    enc_angle14 = r & 0x3FFF;
    prev_angle14 = enc_angle14;
    enc_first_read = 1;
    enc_err_count = 0;
}

uint16_t ENC_ReadRaw(void) {
    /* Full 2-transaction read (reliable: re-sends command each time) */
    spi2_cs_low();
    spi2_xfer(enc_cmd_angle);
    spi2_cs_high();
    spi2_delay_csn();
    spi2_cs_low();
    uint16_t raw = spi2_xfer(AS5048A_NOP);
    spi2_cs_high();
    enc_raw = raw;
    return raw;
}

uint16_t ENC_GetAngle14(void) {
    if(enc_error) return 0xFFFFu;  /* error: parity, EF, or SPI timeout */
    return enc_angle14;
}

int32_t ENC_GetAngle_deg(void) {
    return (int32_t)((uint32_t)enc_angle14 * 360 / 16384);
}

int32_t ENC_GetSpeed_rpm(void) {
    return enc_speed_rpm;
}

uint8_t ENC_GetError(void) {
    return enc_error;
}

void ENC_Update(void) {
    /* 2-transaction read (reliable: re-sends ANGLE command each cycle) */
    spi2_cs_low();
    spi2_xfer(enc_cmd_angle);
    spi2_cs_high();
    spi2_delay_csn();
    spi2_cs_low();
    uint16_t raw = spi2_xfer(AS5048A_NOP);
    spi2_cs_high();

    if(raw == 0xFFFF) {
        enc_err_count++;
        return;  /* SPI timeout — enc_error already set in spi2_xfer */
    }

    /* Error flag (bit 14) */
    if(raw & AS5048A_EF_BIT) {
        enc_error = ENC_ERR_EF;
        enc_err_count++;
        return;
    }

    /* Parity check (even, XOR-tree) */
    if(!parity_ok(raw)) {
        enc_error = ENC_ERR_PARITY;
        enc_err_count++;
        return;
    }
    enc_error = 0;

    enc_raw = raw;
    uint16_t angle = raw & 0x3FFF;
    enc_angle14 = angle;

    /* Speed calculation: delta over 1 ms (TIM6 1 kHz) */
    if(enc_first_read) {
        enc_first_read = 0;
        prev_angle14 = angle;
        enc_speed_rpm = 0;
        return;
    }

    int32_t delta = (int32_t)angle - (int32_t)prev_angle14;
    if(delta >  (ENC_COUNTS_PER_REV / 2)) delta -= ENC_COUNTS_PER_REV;
    if(delta < -(ENC_COUNTS_PER_REV / 2)) delta += ENC_COUNTS_PER_REV;

    int32_t rpm = (delta * 60000) / ENC_COUNTS_PER_REV;

    /* IIR filter 1/8: speed += (new - speed) >> 3 */
    enc_speed_rpm += (rpm - enc_speed_rpm) >> ENC_FILTER_SHIFT;

    prev_angle14 = angle;
}
