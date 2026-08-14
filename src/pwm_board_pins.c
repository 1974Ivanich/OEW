#include "pwm_board_pins.h"
#include "stm32g474xx.h"

/* PB4=EN1, PB5=EN2, PB6=scope trigger. */
#define GATE_EN1_PIN   4u
#define GATE_EN2_PIN   5u
#define TRIGGER_PIN    6u
#define GATE_MASK      ((1u << GATE_EN1_PIN) | (1u << GATE_EN2_PIN))
#define GATE_RESET     (GATE_MASK << 16u)
#define TRIGGER_RESET  (1u << (TRIGGER_PIN + 16u))

static void gpio_set_af(GPIO_TypeDef *port, uint32_t pin, uint32_t af)
{
    const uint32_t shift = (pin & 7u) * 4u;
    volatile uint32_t *const afr = (pin < 8u) ? &port->AFR[0] : &port->AFR[1];

    /* AF push-pull, no pull, high speed. Configure AF before MODER switch. */
    port->OTYPER &= ~(1u << pin);
    port->PUPDR &= ~(3u << (pin * 2u));
    port->OSPEEDR = (port->OSPEEDR & ~(3u << (pin * 2u))) |
                    (3u << (pin * 2u));
    *afr = (*afr & ~(0xFu << shift)) | (af << shift);
    port->MODER = (port->MODER & ~(3u << (pin * 2u))) |
                  (2u << (pin * 2u));
}

static void gpio_set_output_low(GPIO_TypeDef *port, uint32_t pin)
{
    /* Preload ODR low before MODER=output; no active-high EN glitch. */
    port->BSRR = 1u << (pin + 16u);
    port->OTYPER &= ~(1u << pin);
    port->PUPDR &= ~(3u << (pin * 2u));
    port->OSPEEDR = (port->OSPEEDR & ~(3u << (pin * 2u))) |
                    (1u << (pin * 2u)); /* medium speed: EN/TRIG only */
    port->MODER = (port->MODER & ~(3u << (pin * 2u))) |
                  (1u << (pin * 2u));
}

static void gpio_set_analog(GPIO_TypeDef *port, uint32_t pin)
{
    /* ADC input: digital input/output paths and pulls must be disconnected. */
    port->PUPDR &= ~(3u << (pin * 2u));
    port->MODER = (port->MODER & ~(3u << (pin * 2u))) |
                  (3u << (pin * 2u));
}

void PWM_BoardPins_Init(void)
{
    /* Enable all GPIO clocks before touching BSRR/MODER/AFR. */
    RCC->AHB2ENR |= RCC_AHB2ENR_GPIOAEN |
                    RCC_AHB2ENR_GPIOBEN |
                    RCC_AHB2ENR_GPIOCEN;
    (void)RCC->AHB2ENR;

    /* Force both drivers off and scope trigger low before mode transitions. */
    GPIOB->BSRR = GATE_RESET | TRIGGER_RESET;
    __DSB();

    gpio_set_output_low(GPIOB, GATE_EN1_PIN);
    gpio_set_output_low(GPIOB, GATE_EN2_PIN);
    gpio_set_output_low(GPIOB, TRIGGER_PIN);

    /* ADC1/ADC2 board inputs: PA0, PA1, PA6 and PC4. */
    gpio_set_analog(GPIOA, 0u);
    gpio_set_analog(GPIOA, 1u);
    gpio_set_analog(GPIOA, 6u);
    gpio_set_analog(GPIOC, 4u);

    /* USART2 TX/RX: PA2/PA3 AF7; TIM2 encoder CH1: PA15 AF1. */
    gpio_set_af(GPIOA, 2u, 7u);
    gpio_set_af(GPIOA, 3u, 7u);
    gpio_set_af(GPIOA, 15u, 1u);

    /* TIM1 CH1/CH2/CH3: PC0/PC1/PC2 AF2. */
    gpio_set_af(GPIOC, 0u, 2u);
    gpio_set_af(GPIOC, 1u, 2u);
    gpio_set_af(GPIOC, 2u, 2u);

    /* TIM1 complementary channels: PA7, PB0, PB1 AF6. */
    gpio_set_af(GPIOA, 7u, 6u);
    gpio_set_af(GPIOB, 0u, 6u);
    gpio_set_af(GPIOB, 1u, 6u);

    /* TIM8 CH1/2/3 and CH1N/2N/3N: PC6/7/8/10/11/12 AF4. */
    gpio_set_af(GPIOC, 6u, 4u);
    gpio_set_af(GPIOC, 7u, 4u);
    gpio_set_af(GPIOC, 8u, 4u);
    gpio_set_af(GPIOC, 10u, 4u);
    gpio_set_af(GPIOC, 11u, 4u);
    gpio_set_af(GPIOC, 12u, 4u);

    /* Hardware gate drivers remain disabled regardless of timer register state. */
    GPIOB->BSRR = GATE_RESET | TRIGGER_RESET;
    __DSB();
}

void PWM_GatesEnable(void)
{
    GPIOB->BSRR = GATE_MASK;
    __DSB();
}

void PWM_GatesDisable(void)
{
    GPIOB->BSRR = GATE_RESET;
    __DSB();
}

bool PWM_GatesAreEnabled(void)
{
    return (GPIOB->ODR & GATE_MASK) == GATE_MASK;
}

void PWM_TriggerHigh(void)
{
    GPIOB->BSRR = 1u << TRIGGER_PIN;
}

void PWM_TriggerLow(void)
{
    GPIOB->BSRR = TRIGGER_RESET;
}
