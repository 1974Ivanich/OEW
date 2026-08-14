#ifndef STM32G474XX_H
#define STM32G474XX_H

#include <stdint.h>

#define __IO volatile
#define __DMB() do { } while (0)

typedef struct {
    __IO uint32_t ISR;
    __IO uint32_t IER;
    __IO uint32_t CR;
    __IO uint32_t CFGR;
    __IO uint32_t CFGR2;
    __IO uint32_t SMPR1;
    __IO uint32_t SMPR2;
    __IO uint32_t TR1;
    __IO uint32_t TR2;
    __IO uint32_t TR3;
    __IO uint32_t SQR1;
    __IO uint32_t SQR2;
    __IO uint32_t SQR3;
    __IO uint32_t SQR4;
    __IO uint32_t DR;
    __IO uint32_t JSQR;
    __IO uint32_t OFR1;
    __IO uint32_t OFR2;
    __IO uint32_t OFR3;
    __IO uint32_t OFR4;
    __IO uint32_t JDR1;
    __IO uint32_t JDR2;
    __IO uint32_t JDR3;
    __IO uint32_t JDR4;
} ADC_TypeDef;

typedef struct {
    __IO uint32_t CSR;
    __IO uint32_t CCR;
    __IO uint32_t CDR;
} ADC_Common_TypeDef;

typedef struct {
    __IO uint32_t _pad0[19];
    __IO uint32_t AHB2RSTR;
    __IO uint32_t _pad1[10];
    __IO uint32_t AHB2ENR;
} RCC_TypeDef;

typedef struct {
    __IO uint32_t CTRL;
    __IO uint32_t CYCCNT;
} DWT_Type;

extern ADC_TypeDef host_adc1;
extern ADC_TypeDef host_adc2;
extern ADC_Common_TypeDef host_adc12_common;
extern RCC_TypeDef host_rcc;
extern DWT_Type host_dwt;

#define ADC1 (&host_adc1)
#define ADC2 (&host_adc2)
#define ADC12_COMMON (&host_adc12_common)
#define RCC (&host_rcc)
#define DWT (&host_dwt)

#define RCC_AHB2ENR_ADC12EN (1u << 13)
#define RCC_AHB2RSTR_ADC12RST (1u << 13)

#define ADC_CR_ADEN (1u << 0)
#define ADC_CR_ADDIS (1u << 1)
#define ADC_CR_ADSTART (1u << 2)
#define ADC_CR_JADSTART (1u << 3)
#define ADC_CR_ADSTP (1u << 4)
#define ADC_CR_JADSTP (1u << 5)
#define ADC_CR_ADVREGEN (1u << 28)
#define ADC_CR_DEEPPWD (1u << 29)
#define ADC_CR_ADCAL (1u << 31)

#define ADC_ISR_ADRDY (1u << 0)
#define ADC_ISR_EOC (1u << 2)
#define ADC_ISR_EOS (1u << 3)
#define ADC_ISR_OVR (1u << 4)
#define ADC_ISR_JEOS (1u << 6)
#define ADC_ISR_JQOVF (1u << 10)

#define ADC_IER_OVRIE (1u << 4)
#define ADC_IER_JEOSIE (1u << 6)
#define ADC_IER_JQOVFIE (1u << 10)

#define ADC_CFGR_JQDIS (1u << 31)
#define ADC_SQR1_SQ1_Pos 6u
#define ADC_JSQR_JL_Pos 0u
#define ADC_JSQR_JEXTSEL_Pos 2u
#define ADC_JSQR_JEXTEN_Pos 6u
#define ADC_JSQR_JSQ1_Pos 9u
#define ADC_JSQR_JSQ2_Pos 15u
#define ADC_JSQR_JSQ3_Pos 21u
#define ADC_CCR_DUAL_0 (1u << 0)
#define ADC_CCR_DUAL_2 (1u << 2)
#define ADC_CCR_CKMODE_Pos 16u

#endif /* STM32G474XX_H */
