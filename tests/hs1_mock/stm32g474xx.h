#ifndef STM32G474XX_H
#define STM32G474XX_H

#include <stdint.h>

#define __DMB() do { } while (0)
#define __DSB() do { } while (0)
extern uint32_t host_primask;
extern void HostIrqRestoreHook(uint32_t restored_primask);
#define __get_PRIMASK() (host_primask)
#define __disable_irq() do { host_primask = 1u; } while (0)
#define __set_PRIMASK(value) do { \
    host_primask = (uint32_t)(value); \
    if (host_primask == 0u) HostIrqRestoreHook(host_primask); \
} while (0)
#define __enable_irq() __set_PRIMASK(0u)

typedef struct {
    volatile uint32_t CR1;
    volatile uint32_t CR2;
    volatile uint32_t SMCR;
    volatile uint32_t DIER;
    volatile uint32_t SR;
    volatile uint32_t EGR;
    volatile uint32_t CCMR1;
    volatile uint32_t CCMR2;
    volatile uint32_t CCER;
    volatile uint32_t CNT;
    volatile uint32_t PSC;
    volatile uint32_t ARR;
    volatile uint32_t RCR;
    volatile uint32_t CCR1;
    volatile uint32_t CCR2;
    volatile uint32_t CCR3;
    volatile uint32_t BDTR;
    volatile uint32_t DCR1;
    volatile uint32_t DCR2;
    volatile uint32_t DMAR;
    volatile uint32_t OR1;
    volatile uint32_t CCMR3;
    volatile uint32_t CCR4;
    volatile uint32_t CCR5;
    volatile uint32_t CCR6;
    volatile uint32_t AF1;
} TIM_TypeDef;

typedef struct {
    volatile uint32_t MODER;
    volatile uint32_t OTYPER;
    volatile uint32_t OSPEEDR;
    volatile uint32_t PUPDR;
    volatile uint32_t IDR;
    volatile uint32_t ODR;
    volatile uint32_t BSRR;
    volatile uint32_t LCKR;
    volatile uint32_t AFR[2];
} GPIO_TypeDef;

typedef struct {
    volatile uint32_t ISR;
    volatile uint32_t IER;
    volatile uint32_t CR;
    volatile uint32_t CFGR;
    volatile uint32_t CFGR2;
    volatile uint32_t SMPR1;
    volatile uint32_t SMPR2;
    volatile uint32_t TR1;
    volatile uint32_t TR2;
    volatile uint32_t TR3;
    volatile uint32_t SQR1;
    volatile uint32_t SQR2;
    volatile uint32_t SQR3;
    volatile uint32_t SQR4;
    volatile uint32_t DR;
    volatile uint32_t JSQR;
    volatile uint32_t OFR1;
    volatile uint32_t OFR2;
    volatile uint32_t OFR3;
    volatile uint32_t OFR4;
    volatile uint32_t JDR1;
    volatile uint32_t JDR2;
    volatile uint32_t JDR3;
    volatile uint32_t JDR4;
} ADC_TypeDef;

typedef struct {
    volatile uint32_t CSR;
    volatile uint32_t CCR;
    volatile uint32_t CDR;
} ADC_Common_TypeDef;

typedef struct {
    volatile uint32_t _pad0[18];
    volatile uint32_t AHB2ENR;
    volatile uint32_t _pad1[16];
    volatile uint32_t APB2ENR;
    volatile uint32_t _pad2[2];
    volatile uint32_t CFGR;
} RCC_TypeDef;

extern TIM_TypeDef host_tim1;
extern TIM_TypeDef host_tim8;
extern GPIO_TypeDef host_gpioa;
extern GPIO_TypeDef host_gpiob;
extern GPIO_TypeDef host_gpioc;
extern GPIO_TypeDef host_gpiod;
extern ADC_TypeDef host_adc1;
extern ADC_TypeDef host_adc2;
extern ADC_Common_TypeDef host_adc12_common;
extern RCC_TypeDef host_rcc;
extern uint32_t SystemCoreClock;

#define TIM1 (&host_tim1)
#define TIM8 (&host_tim8)
#define GPIOA (&host_gpioa)
#define GPIOB (&host_gpiob)
#define GPIOC (&host_gpioc)
#define GPIOD (&host_gpiod)
#define ADC1 (&host_adc1)
#define ADC2 (&host_adc2)
#define ADC12_COMMON (&host_adc12_common)
#define RCC (&host_rcc)

#define RCC_CFGR_PPRE2 (7u << 11)
#define RCC_CFGR_PPRE2_Pos 11u
#define RCC_AHB2ENR_GPIOAEN (1u << 0)
#define RCC_AHB2ENR_GPIOBEN (1u << 1)
#define RCC_AHB2ENR_GPIOCEN (1u << 2)
#define RCC_AHB2ENR_GPIODEN (1u << 3)
#define RCC_APB2ENR_TIM1EN (1u << 11)
#define RCC_APB2ENR_TIM8EN (1u << 13)

/* ADC12 CCR CKMODE field (bits 31:30) and CFGR RES bits (3:2). */
#define ADC_CCR_CKMODE_Pos 30u
#define ADC_CCR_CKMODE (3u << ADC_CCR_CKMODE_Pos)
#define ADC_CFGR_RES_Msk (3u << 2)
#define ADC_CFGR_RES_Pos 2u

#define TIM_CR1_CEN (1u << 0)
#define TIM_CR1_CMS_0 (1u << 5)
#define TIM_CR1_CMS_1 (1u << 6)
#define TIM_CR1_ARPE (1u << 7)
#define TIM_CR2_MMS_Pos 4u
#define TIM_DIER_BIE (1u << 7)
#define TIM_BDTR_OSSI (1u << 10)
#define TIM_BDTR_OSSR (1u << 11)
#define TIM_BDTR_BKE (1u << 12)
#define TIM_BDTR_BKP (1u << 13)
#define TIM_BDTR_AOE (1u << 14)
#define TIM_BDTR_MOE (1u << 15)
#define TIM_BDTR_BK2E (1u << 24)
#define TIM_SR_BIF (1u << 7)
#define TIM_SR_B2IF (1u << 8)
#define TIM_CCMR1_OC1M_Pos 4u
#define TIM_CCMR1_OC1PE (1u << 3)
#define TIM_CCMR1_OC2M_Pos 12u
#define TIM_CCMR1_OC2PE (1u << 11)
#define TIM_CCMR2_OC3M_Pos 4u
#define TIM_CCMR2_OC3PE (1u << 3)
#define TIM_CCER_CC1E (1u << 0)
#define TIM_CCER_CC1NE (1u << 2)
#define TIM_CCER_CC2E (1u << 4)
#define TIM_CCER_CC2NE (1u << 6)
#define TIM_CCER_CC3E (1u << 8)
#define TIM_CCER_CC3NE (1u << 10)
#define TIM_EGR_UG (1u << 0)

#endif /* STM32G474XX_H */
