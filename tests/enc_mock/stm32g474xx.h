#ifndef STM32G474XX_H
#define STM32G474XX_H
#include <stdint.h>

typedef struct {
    volatile uint32_t CR1, CR2, SMCR, DIER, SR, EGR, CCMR1, CCMR2, CCER;
    volatile uint32_t CNT, PSC, ARR, RCR, CCR1, CCR2, CCR3, BDTR;
    volatile uint32_t DCR1, DCR2, DMAR, OR1, CCMR3, CCR4, CCR5, CCR6, AF1;
} TIM_TypeDef;
typedef struct {
    volatile uint32_t MODER, OTYPER, OSPEEDR, PUPDR, IDR, ODR, BSRR, LCKR, AFR[2];
} GPIO_TypeDef;
typedef struct {
    volatile uint32_t CFGR, AHB1ENR, AHB2ENR, AHB3ENR, APB1ENR1, APB1ENR2, APB2ENR;
} RCC_TypeDef;
extern TIM_TypeDef host_tim2;
extern GPIO_TypeDef host_gpioa;
extern RCC_TypeDef host_rcc;
extern uint32_t SystemCoreClock;
#define TIM2 (&host_tim2)
#define GPIOA (&host_gpioa)
#define RCC (&host_rcc)
#define TIM2_IRQn 28
#define RCC_AHB2ENR_GPIOAEN (1u << 0)
#define RCC_APB1ENR1_TIM2EN (1u << 0)
#define RCC_CFGR_PPRE1 (7u << 8)
#define RCC_CFGR_PPRE1_Pos 8u
#define TIM_CR1_CEN (1u << 0)
#define TIM_DIER_CC1IE (1u << 1)
#define TIM_SR_CC1IF (1u << 1)
#define TIM_SR_CC2IF (1u << 2)
#define TIM_SR_CC1OF (1u << 9)
#define TIM_SR_CC2OF (1u << 10)
#define TIM_CCMR1_CC1S_Pos 0u
#define TIM_CCMR1_IC1F_Pos 4u
#define TIM_CCMR1_CC2S_Pos 8u
#define TIM_CCMR1_IC2F_Pos 12u
#define TIM_CCER_CC1E (1u << 0)
#define TIM_CCER_CC2E (1u << 4)
#define TIM_CCER_CC2P (1u << 5)
#define TIM_SMCR_TS_Pos 4u
#define TIM_SMCR_SMS_Pos 0u
static inline void NVIC_SetPriority(int irq, uint32_t priority) { (void)irq; (void)priority; }
static inline void NVIC_EnableIRQ(int irq) { (void)irq; }
#endif
