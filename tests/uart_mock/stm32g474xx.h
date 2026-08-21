#ifndef STM32G474XX_H
#define STM32G474XX_H
#include <stdint.h>

typedef struct {
    volatile uint32_t CR1;
    volatile uint32_t CR2;
    volatile uint32_t CR3;
    volatile uint32_t BRR;
    volatile uint32_t GTPR;
    volatile uint32_t RTOR;
    volatile uint32_t RQR;
    volatile uint32_t ISR;
    volatile uint32_t ICR;
    volatile uint32_t RDR;
    volatile uint32_t TDR;
} USART_TypeDef;
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
    volatile uint32_t CFGR;
    volatile uint32_t AHB1ENR;
    volatile uint32_t AHB2ENR;
    volatile uint32_t AHB3ENR;
    volatile uint32_t APB1ENR1;
    volatile uint32_t APB1ENR2;
    volatile uint32_t APB2ENR;
} RCC_TypeDef;
extern USART_TypeDef host_usart2;
extern GPIO_TypeDef host_gpioa;
extern RCC_TypeDef host_rcc;
extern uint32_t host_primask;
extern uint32_t SystemCoreClock;
#define USART2 (&host_usart2)
#define GPIOA (&host_gpioa)
#define RCC (&host_rcc)
#define USART2_IRQn 38
#define RCC_APB1ENR1_USART2EN (1u << 17)
#define RCC_AHB2ENR_GPIOAEN (1u << 0)
#define RCC_CFGR_PPRE1 (7u << 8)
#define RCC_CFGR_PPRE1_Pos 8u
#define USART_CR1_UE (1u << 0)
#define USART_CR1_RE (1u << 2)
#define USART_CR1_TE (1u << 3)
#define USART_CR1_RXNEIE_RXFNEIE (1u << 5)
#define USART_CR1_TXEIE (1u << 7)
#define USART_CR1_PEIE (1u << 8)
#define USART_ISR_PE (1u << 0)
#define USART_ISR_FE (1u << 1)
#define USART_ISR_NE (1u << 2)
#define USART_ISR_ORE (1u << 3)
#define USART_ISR_RXNE_RXFNE (1u << 5)
#define USART_ISR_TXE_TXFNF (1u << 7)
#define USART_ICR_PECF (1u << 0)
#define USART_ICR_FECF (1u << 1)
#define USART_ICR_NECF (1u << 2)
#define USART_ICR_ORECF (1u << 3)
static inline void __DMB(void) { }
static inline void __DSB(void) { }
static inline uint32_t __get_PRIMASK(void) { return host_primask; }
static inline void __disable_irq(void) { host_primask = 1u; }
static inline void __set_PRIMASK(uint32_t value) { host_primask = value; }
static inline void NVIC_SetPriority(int irq, uint32_t priority) { (void)irq; (void)priority; }
static inline void NVIC_EnableIRQ(int irq) { (void)irq; }
#endif
