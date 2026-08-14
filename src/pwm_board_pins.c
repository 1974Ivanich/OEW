#include "pwm_board_pins.h"
#include "stm32g474xx.h"

#if !defined(RCC_AHB2ENR_GPIODEN)
#error "OEW-HS-1 requires GPIO D clock support for PD2 TIM8_BKIN."
#endif

/* OEW-HS-1 interposer signals. PB4/PB5 are ARM_REQ only; they are no longer
 * direct IPM enable pins. */
#define ARM_REQ_A_PIN      4u   /* PB4 */
#define ARM_REQ_B_PIN      5u   /* PB5 */
#define TRIGGER_PIN        6u   /* PB6 */
#define SAFETY_OK_PIN     11u   /* PB11, external latch feedback */
#define TIM1_BKIN_PIN     12u   /* PB12 AF6, FAULT_N */
#define MCU_HEARTBEAT_PIN 13u   /* PB13, external watchdog */
#define TIM8_BKIN_PIN      2u   /* PD2 AF4, FAULT_N */

#define ARM_MASK      ((1u << ARM_REQ_A_PIN) | (1u << ARM_REQ_B_PIN))
#define ARM_RESET     (ARM_MASK << 16u)
#define TRIGGER_RESET (1u << (TRIGGER_PIN + 16u))
#define HEARTBEAT_RESET (1u << (MCU_HEARTBEAT_PIN + 16u))

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
    port->PUPDR &= ~(3u << (pin * 2u));
    port->OSPEEDR = (port->OSPEEDR & ~(3u << (pin * 2u))) |
                    (3u << (pin * 2u));
    *afr = (*afr & ~(0xFu << shift)) | (af << shift);
    port->MODER = (port->MODER & ~(3u << (pin * 2u))) |
                  (2u << (pin * 2u));
}

static void gpio_set_input(GPIO_TypeDef *port, uint32_t pin)
{
    port->PUPDR &= ~(3u << (pin * 2u));
    port->MODER &= ~(3u << (pin * 2u));
}

static void gpio_set_output_low(GPIO_TypeDef *port, uint32_t pin)
{
    /* Preload low before MODER=output: no positive ARM_REQ/HB glitch. */
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

    /* Safe before all mode changes: ARM_REQ, trigger and heartbeat low. */
    gpio_bsrr_write(GPIOB, ARM_RESET | TRIGGER_RESET | HEARTBEAT_RESET);
    __DSB();

    gpio_set_output_low(GPIOB, ARM_REQ_A_PIN);
    gpio_set_output_low(GPIOB, ARM_REQ_B_PIN);
    gpio_set_output_low(GPIOB, TRIGGER_PIN);
    gpio_set_output_low(GPIOB, MCU_HEARTBEAT_PIN);

    /* Safety feedback and external break nets have no internal pull; the
     * OEW-HS-1 interposer supplies fail-low external 47 kΩ biases. */
    gpio_set_input(GPIOB, SAFETY_OK_PIN);
    gpio_set_af(GPIOB, TIM1_BKIN_PIN, 6u);  /* PB12 AF6: TIM1_BKIN */
    gpio_set_af(GPIOD, TIM8_BKIN_PIN, 4u);  /* PD2 AF4: TIM8_BKIN */

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

    /* TIM8 PWM and complements. PC8 remains TIM8_CH3, never heartbeat. */
    gpio_set_af(GPIOC, 6u, 4u);
    gpio_set_af(GPIOC, 7u, 4u);
    gpio_set_af(GPIOC, 8u, 4u);
    gpio_set_af(GPIOC, 10u, 4u);
    gpio_set_af(GPIOC, 11u, 4u);
    gpio_set_af(GPIOC, 12u, 4u);

    PWM_ArmRequestsDisable();
    PWM_TriggerLow();
    PWM_BoardHeartbeatLow();
}

void PWM_ArmRequestsEnable(void)
{
    gpio_bsrr_write(GPIOB, ARM_MASK);
    __DSB();
}

void PWM_ArmRequestsDisable(void)
{
    gpio_bsrr_write(GPIOB, ARM_RESET);
    __DSB();
}

bool PWM_ArmRequestsAsserted(void)
{
    return (GPIOB->ODR & ARM_MASK) == ARM_MASK;
}

bool PWM_SafetyOkIsHigh(void)
{
    return (GPIOB->IDR & (1u << SAFETY_OK_PIN)) != 0u;
}

bool PWM_BreakInputsAreHigh(void)
{
    return ((GPIOB->IDR & (1u << TIM1_BKIN_PIN)) != 0u) &&
           ((GPIOD->IDR & (1u << TIM8_BKIN_PIN)) != 0u);
}

void PWM_BoardHeartbeatToggle(void)
{
    if ((GPIOB->ODR & (1u << MCU_HEARTBEAT_PIN)) != 0u) {
        gpio_bsrr_write(GPIOB, HEARTBEAT_RESET);
    } else {
        gpio_bsrr_write(GPIOB, 1u << MCU_HEARTBEAT_PIN);
    }
    __DSB();
}

void PWM_BoardHeartbeatLow(void)
{
    gpio_bsrr_write(GPIOB, HEARTBEAT_RESET);
    __DSB();
}

/* Compatibility shims: these only assert/deassert interposer ARM_REQ. */
void PWM_GatesEnable(void) { PWM_ArmRequestsEnable(); }
void PWM_GatesDisable(void) { PWM_ArmRequestsDisable(); }
bool PWM_GatesAreEnabled(void) { return PWM_ArmRequestsAsserted(); }

void PWM_TriggerHigh(void)
{
    gpio_bsrr_write(GPIOB, 1u << TRIGGER_PIN);
}

void PWM_TriggerLow(void)
{
    gpio_bsrr_write(GPIOB, TRIGGER_RESET);
}
