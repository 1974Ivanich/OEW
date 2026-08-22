#include "pwm_board_pins.h"
#include "stm32g474xx.h"

#if !defined(RCC_AHB2ENR_GPIODEN)
#error "SD-direct protection requires GPIO D clock support for PD2 TIM8_BKIN."
#endif

/* Direct STEVAL SD nets. Both are external open-drain fault outputs and must
 * never be configured as MCU outputs or given an internal pull. */
#define TRIGGER_PIN      6u   /* PB6, vflog/scope trigger */
#define SD1_BKIN_PIN    12u   /* PB12 AF6, TIM1_BKIN, active-low SD1 */
#define SD2_BKIN_PIN     2u   /* PD2 AF4, TIM8_BKIN, active-low SD2 */

#define TRIGGER_RESET (1u << (TRIGGER_PIN + 16u))

static void gpio_bsrr_write(GPIO_TypeDef *port, uint32_t bits)
{
    port->BSRR = bits;
#ifdef PWM_HOST_TEST
    port->ODR = (port->ODR | (bits & 0xFFFFu)) & ~(bits >> 16u);
#endif
}

static void gpio_set_af(GPIO_TypeDef *port, uint32_t pin, uint32_t af)
{
    const uint32_t shift = (pin & 7u) * 4u;
    volatile uint32_t *const afr = (pin < 8u) ? &port->AFR[0] : &port->AFR[1];

    port->OTYPER &= ~(1u << pin);
    port->PUPDR &= ~(3u << (pin * 2u)); /* no MCU pull on external SD net */
    port->OSPEEDR = (port->OSPEEDR & ~(3u << (pin * 2u))) |
                    (3u << (pin * 2u));
    *afr = (*afr & ~(0xFu << shift)) | (af << shift);
    port->MODER = (port->MODER & ~(3u << (pin * 2u))) |
                  (2u << (pin * 2u));
}

static void gpio_set_output_low(GPIO_TypeDef *port, uint32_t pin)
{
    gpio_bsrr_write(port, 1u << (pin + 16u));
    port->OTYPER &= ~(1u << pin);
    port->PUPDR &= ~(3u << (pin * 2u));
    port->OSPEEDR = (port->OSPEEDR & ~(3u << (pin * 2u))) |
                    (1u << (pin * 2u));
    port->MODER = (port->MODER & ~(3u << (pin * 2u))) |
                  (1u << (pin * 2u));
}

static void gpio_set_analog(GPIO_TypeDef *port, uint32_t pin)
{
    port->PUPDR &= ~(3u << (pin * 2u));
    port->MODER = (port->MODER & ~(3u << (pin * 2u))) |
                  (3u << (pin * 2u));
}

void PWM_BoardPins_Init(void)
{
    RCC->AHB2ENR |= RCC_AHB2ENR_GPIOAEN |
                    RCC_AHB2ENR_GPIOBEN |
                    RCC_AHB2ENR_GPIOCEN |
                    RCC_AHB2ENR_GPIODEN;
    (void)RCC->AHB2ENR;

    /* The only board GPIO output owned by this module is the harmless
     * scope/vflog trigger. SD is never driven by the MCU. */
    gpio_bsrr_write(GPIOB, TRIGGER_RESET);
    __DSB();
    gpio_set_output_low(GPIOB, TRIGGER_PIN);

    /* Direct SD→BKIN: AF input and IDR readback; no internal pull. */
    gpio_set_af(GPIOB, SD1_BKIN_PIN, 6u);  /* PB12 AF6: TIM1_BKIN / SD1 */
    gpio_set_af(GPIOD, SD2_BKIN_PIN, 4u);  /* PD2 AF4: TIM8_BKIN / SD2 */

    /* ADC1/ADC2 board inputs. */
    gpio_set_analog(GPIOA, 0u);
    gpio_set_analog(GPIOA, 1u);
    gpio_set_analog(GPIOA, 6u);
    gpio_set_analog(GPIOC, 4u);

    /* USART2 and encoder. */
    gpio_set_af(GPIOA, 2u, 7u);
    gpio_set_af(GPIOA, 3u, 7u);
    gpio_set_af(GPIOA, 15u, 1u);

    /* TIM1 PWM and complements. */
    gpio_set_af(GPIOC, 0u, 2u);
    gpio_set_af(GPIOC, 1u, 2u);
    gpio_set_af(GPIOC, 2u, 2u);
    gpio_set_af(GPIOA, 7u, 6u);
    gpio_set_af(GPIOB, 0u, 6u);
    gpio_set_af(GPIOB, 1u, 6u);

    /* TIM8 PWM and complements. PC8 remains TIM8_CH3. */
    gpio_set_af(GPIOC, 6u, 4u);
    gpio_set_af(GPIOC, 7u, 4u);
    gpio_set_af(GPIOC, 8u, 4u);
    gpio_set_af(GPIOC, 10u, 4u);
    gpio_set_af(GPIOC, 11u, 4u);
    gpio_set_af(GPIOC, 12u, 4u);

    PWM_TriggerLow();
}

bool PWM_SdLinesAreHigh(void)
{
    return ((GPIOB->IDR & (1u << SD1_BKIN_PIN)) != 0u) &&
           ((GPIOD->IDR & (1u << SD2_BKIN_PIN)) != 0u);
}

bool PWM_EmStop1IsHigh(void)
{
    return (GPIOB->IDR & (1u << SD1_BKIN_PIN)) != 0u;
}

bool PWM_EmStop2IsHigh(void)
{
    return (GPIOD->IDR & (1u << SD2_BKIN_PIN)) != 0u;
}

void PWM_TriggerHigh(void)
{
    gpio_bsrr_write(GPIOB, 1u << TRIGGER_PIN);
}

void PWM_TriggerLow(void)
{
    gpio_bsrr_write(GPIOB, TRIGGER_RESET);
}
