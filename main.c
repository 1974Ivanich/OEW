#include "stm32g474xx.h"
#include <stdio.h>

#define TIM_PSC                 15
#define TIM_ARR                 99
#define TIM_DTG                 16
#define ADC_TIMEOUT             100000UL
#define DELAY_10MS              160000UL
#define DELAY_1S                16000000UL

#define ADC_VREF_MV             3265U
#define ADC_MAX_CODE            4095U
#define VBUS_DIV_NUM            2440U
#define VBUS_DIV_DEN            100U
#define CS_GAIN                 2U
#define RSHUNT_MOHM             30U
#define ADC_AVG_SAMPLES         16U
#define TEST_DUTY_CCR           15U

static void delay_volatile(volatile uint32_t count) { while (count--) { __NOP(); } }
void Measure_Phase(char phase);

void USART2_SendString(char *str) {
    while (*str) { while (!(USART2->ISR & USART_ISR_TXE_TXFNF)) {} USART2->TDR = (uint8_t)(*str++); }
}
char USART2_ReceiveChar(void) {
    while (!(USART2->ISR & USART_ISR_RXNE_RXFNE)) {} return (char)USART2->RDR;
}
static void Show_Menu(void) {
    USART2_SendString("--- Menu ---\r\n1.U 2.V 3.W M.menu Q.cycle\r\nEnter: ");
}

static void USART2_Init(void) {
    RCC->APB1ENR1 |= RCC_APB1ENR1_USART2EN;
    RCC->AHB2ENR |= RCC_AHB2ENR_GPIOAEN;
    GPIOA->MODER &= ~(3U << 4); GPIOA->MODER |= (2U << 4);
    GPIOA->AFR[0] &= ~(0xF << 8); GPIOA->AFR[0] |= (7U << 8);
    GPIOA->MODER &= ~(3U << 6); GPIOA->MODER |= (2U << 6);
    GPIOA->AFR[0] &= ~(0xF << 12); GPIOA->AFR[0] |= (7U << 12);
    USART2->BRR = 16000000UL / 115200;
    USART2->CR1 = USART_CR1_TE | USART_CR1_RE | USART_CR1_UE;
}

static void GPIO_Init(void) {
    RCC->AHB2ENR |= RCC_AHB2ENR_GPIOAEN | RCC_AHB2ENR_GPIOBEN | RCC_AHB2ENR_GPIOCEN;

    /* TIM1: PC0/PC1/PC2 (AF2) */
    GPIOC->MODER &= ~((3U<<0)|(3U<<2)|(3U<<4));
    GPIOC->MODER |=  (2U<<0)|(2U<<2)|(2U<<4);
    GPIOC->AFR[0] &= ~((0xF<<0)|(0xF<<4)|(0xF<<8));
    GPIOC->AFR[0] |=  (2U<<0)|(2U<<4)|(2U<<8);

    /* TIM1N: PA7=CH1N, PB0=CH2N, PB1=CH3N (AF6) */
    GPIOA->MODER &= ~(3U<<14); GPIOA->MODER |= (2U<<14);
    GPIOA->AFR[0] &= ~(0xF<<28); GPIOA->AFR[0] |= (6U<<28);

    GPIOB->MODER &= ~((3U<<0)|(3U<<2)); GPIOB->MODER |= (2U<<0)|(2U<<2);
    GPIOB->AFR[0] &= ~((0xF<<0)|(0xF<<4)); GPIOB->AFR[0] |= (6U<<0)|(6U<<4);

    /* TIM8: PC6/PC7/PC8 (AF4) */
    GPIOC->MODER &= ~((3U<<12)|(3U<<14)|(3U<<16));
    GPIOC->MODER |=  (2U<<12)|(2U<<14)|(2U<<16);
    GPIOC->AFR[0] &= ~((0xF<<24)|(0xF<<28));
    GPIOC->AFR[0] |=  (4U<<24)|(4U<<28);
    GPIOC->AFR[1] &= ~(0xF<<0); GPIOC->AFR[1] |= (4U<<0);

    /* TIM8N: PC10/PC11/PC12 (AF4) */
    GPIOC->MODER &= ~((3U<<20)|(3U<<22)|(3U<<24));
    GPIOC->MODER |=  (2U<<20)|(2U<<22)|(2U<<24);
    GPIOC->AFR[1] &= ~((0xF<<8)|(0xF<<12)|(0xF<<16));
    GPIOC->AFR[1] |=  (4U<<8)|(4U<<12)|(4U<<16);

    /* EN */
    GPIOB->MODER &= ~((3U<<8)|(3U<<10)); GPIOB->MODER |= (1U<<8)|(1U<<10);
    GPIOB->BSRR = (1U<<20)|(1U<<21);

    /* ADC2: PA0/PA1/PA6/PC4 (analog) */
    GPIOA->MODER |= (3U<<0)|(3U<<2)|(3U<<12);
    GPIOC->MODER |= (3U<<8);
}

static void ADC2_Init(void) {
    uint32_t t;
    RCC->AHB2ENR |= RCC_AHB2ENR_ADC12EN; delay_volatile(10000);
    RCC->AHB2RSTR |= RCC_AHB2RSTR_ADC12RST; RCC->AHB2RSTR &= ~RCC_AHB2RSTR_ADC12RST; delay_volatile(1000);
    ADC12_COMMON->CCR = (1U<<16);
    ADC2->CR = 0; ADC2->CR &= ~ADC_CR_DEEPPWD; ADC2->CR |= ADC_CR_ADVREGEN; delay_volatile(100000);
    ADC2->CR |= ADC_CR_ADCAL; t=1000000; while(ADC2->CR&ADC_CR_ADCAL) { if(--t==0)return; }
    ADC2->CFGR = 0;
    ADC2->SMPR1 |= (7U<<ADC_SMPR1_SMP1_Pos)|(7U<<ADC_SMPR1_SMP2_Pos)|(7U<<ADC_SMPR1_SMP3_Pos)|(7U<<ADC_SMPR1_SMP5_Pos);
    ADC2->ISR = ADC_ISR_ADRDY; ADC2->CR |= ADC_CR_ADEN; t=1000000; while(!(ADC2->ISR&ADC_ISR_ADRDY)){if(--t==0)return;}
}

static void ADC2_Stop(void) {
    if(ADC2->CR&ADC_CR_ADSTART){ADC2->CR|=ADC_CR_ADSTP;uint32_t t=100000;while(ADC2->CR&ADC_CR_ADSTP){if(--t==0)break;}}
    ADC2->ISR = ADC_ISR_OVR;
}

uint16_t ADC2_Read(uint32_t ch) {
    uint32_t t=1000000; if(!(ADC2->CR&ADC_CR_ADEN))return 0xFFFD;
    if(ADC2->CR&ADC_CR_ADSTART){ADC2->CR|=ADC_CR_ADSTP;t=100000;while(ADC2->CR&ADC_CR_ADSTP){if(--t==0)break;}}
    ADC2->SQR1=(ch<<ADC_SQR1_SQ1_Pos); ADC2->ISR=(ADC_ISR_EOC|ADC_ISR_EOS|ADC_ISR_OVR); ADC2->CR|=ADC_CR_ADSTART;
    while(!(ADC2->ISR&ADC_ISR_EOC)){if(--t==0)return 0xFFFF;}
    uint16_t r=(uint16_t)(ADC2->DR); ADC2_Stop(); return r;
}

uint16_t ADC2_ReadAvg(uint32_t ch, uint8_t n) {
    uint32_t a=0; for(uint8_t i=0;i<n;i++){uint16_t v=ADC2_Read(ch);if(v==0xFFFF||v==0xFFFD)return v;a+=v;}
    return (uint16_t)(a/n);
}

static uint32_t ADC_CodeToMillivolts(uint16_t code) { return ((uint32_t)code*ADC_VREF_MV)/ADC_MAX_CODE; }
static uint32_t Vbus_RealMillivolts(uint16_t code) { return (ADC_CodeToMillivolts(code)*VBUS_DIV_NUM)/VBUS_DIV_DEN; }
static int32_t CurrentDeltaToMilliamps(int32_t delta) {
    int32_t mv=(delta*(int32_t)ADC_VREF_MV)/(int32_t)ADC_MAX_CODE;
    return (mv*1000)/((int32_t)RSHUNT_MOHM*(int32_t)CS_GAIN);
}

static void TIM1_Init(void) {
    RCC->APB2ENR|=RCC_APB2ENR_TIM1EN; TIM1->PSC=TIM_PSC;TIM1->ARR=TIM_ARR;
    TIM1->CR1=TIM_CR1_CMS_1; TIM1->BDTR=TIM_DTG|TIM_BDTR_AOE;
    TIM1->CCMR1|=(6U<<TIM_CCMR1_OC1M_Pos)|TIM_CCMR1_OC1PE|(6U<<TIM_CCMR1_OC2M_Pos)|TIM_CCMR1_OC2PE;
    TIM1->CCMR2|=(6U<<TIM_CCMR2_OC3M_Pos)|TIM_CCMR2_OC3PE;
    TIM1->CCR1=0;TIM1->CCR2=0;TIM1->CCR3=0; TIM1->CCER=0; TIM1->EGR|=TIM_EGR_UG;
}
static void TIM8_Init(void) {
    RCC->APB2ENR|=RCC_APB2ENR_TIM8EN; TIM8->PSC=TIM_PSC;TIM8->ARR=TIM_ARR;
    TIM8->CR1=TIM_CR1_CMS_1; TIM8->BDTR=TIM_DTG|TIM_BDTR_AOE;
    TIM8->CCMR1|=(6U<<TIM_CCMR1_OC1M_Pos)|TIM_CCMR1_OC1PE|(6U<<TIM_CCMR1_OC2M_Pos)|TIM_CCMR1_OC2PE;
    TIM8->CCMR2|=(6U<<TIM_CCMR2_OC3M_Pos)|TIM_CCMR2_OC3PE;
    TIM8->CCR1=0;TIM8->CCR2=0;TIM8->CCR3=0; TIM8->CCER=0; TIM8->EGR|=TIM_EGR_UG;
}

static void Execute_Command(char cmd) {
    if(cmd=='1')Measure_Phase('U'); else if(cmd=='2')Measure_Phase('V');
    else if(cmd=='3')Measure_Phase('W'); else if(cmd=='M'||cmd=='m')Show_Menu();
    else if(cmd=='Q'||cmd=='q')USART2_SendString("Auto cycle\r\n");
    else USART2_SendString("?\r\n");
}

void Measure_Phase(char phase) {
    char buf[160];
    uint16_t i_offset = ADC2_ReadAvg(1,16);
    GPIOB->BSRR = (1<<4)|(1<<5); TIM1->CCER=0;TIM8->CCER=0; TIM1->CNT=0;TIM8->CNT=0;

    if(phase=='U'){TIM1->CCR1=TEST_DUTY_CCR;TIM1->CCER=TIM_CCER_CC1E|TIM_CCER_CC1NE;TIM8->CCER=TIM_CCER_CC1NE|TIM_CCER_CC2NE|TIM_CCER_CC3NE;}
    else if(phase=='V'){TIM1->CCR2=TEST_DUTY_CCR;TIM1->CCER=TIM_CCER_CC2E|TIM_CCER_CC2NE;TIM8->CCER=TIM_CCER_CC1NE|TIM_CCER_CC2NE|TIM_CCER_CC3NE;}
    else if(phase=='W'){TIM1->CCR3=TEST_DUTY_CCR;TIM1->CCER=TIM_CCER_CC3E|TIM_CCER_CC3NE;TIM8->CCER=TIM_CCER_CC1NE|TIM_CCER_CC2NE|TIM_CCER_CC3NE;}

    TIM1->BDTR|=TIM_BDTR_MOE;TIM8->BDTR|=TIM_BDTR_MOE; TIM1->CR1|=TIM_CR1_CEN;TIM8->CR1|=TIM_CR1_CEN;
    delay_volatile(DELAY_10MS);

    /* ADC DIAG */
    { char dd[80]; uint16_t d1=ADC2_ReadAvg(1,16); uint16_t d2=ADC2_ReadAvg(2,16); uint16_t d3=ADC2_ReadAvg(3,16); uint16_t d5=ADC2_ReadAvg(5,16);
      uint32_t mv1=(uint32_t)d1*ADC_VREF_MV/ADC_MAX_CODE; uint32_t mv2=(uint32_t)d2*ADC_VREF_MV/ADC_MAX_CODE;
      uint32_t mv3=(uint32_t)d3*ADC_VREF_MV/ADC_MAX_CODE; uint32_t mv5=(uint32_t)d5*ADC_VREF_MV/ADC_MAX_CODE;
      sprintf(dd,"ADC: I1=%u(%lumV) I2=%u(%lumV) IN=%u(%lumV) VBUS=%u(%lumV)\r\n",d1,mv1,d2,mv2,d3,mv3,d5,mv5); USART2_SendString(dd); }

    uint16_t raw_i1   = ADC2_ReadAvg(1, ADC_AVG_SAMPLES);
    uint16_t raw_vbus = ADC2_ReadAvg(5, ADC_AVG_SAMPLES);

    TIM1->CR1&=~TIM_CR1_CEN;TIM8->CR1&=~TIM_CR1_CEN; TIM1->BDTR&=~TIM_BDTR_MOE;TIM8->BDTR&=~TIM_BDTR_MOE;
    GPIOB->BSRR=(1<<20)|(1<<21);

    if(raw_vbus==0xFFFF||raw_i1==0xFFFF){USART2_SendString("ADC err\r\n");return;}

    uint32_t vbus_mv=Vbus_RealMillivolts(raw_vbus);
    int32_t i_delta=(int32_t)raw_i1-(int32_t)i_offset;
    int32_t i_ma=CurrentDeltaToMilliamps(i_delta);
    uint32_t v_applied_mv=(vbus_mv*TEST_DUTY_CCR)/(TIM_ARR+1U);

    if(i_ma<=0){sprintf(buf,"Phase %c: Vbus=%lu I invalid(raw=%d off=%d)\r\n",phase,(unsigned long)vbus_mv,raw_i1,i_offset);USART2_SendString(buf);return;}
    uint32_t r_mohm=(v_applied_mv*1000UL)/(uint32_t)i_ma;
    sprintf(buf,"@MEAS:%c:Vbus=%lu:Uwnd=%lu:I=%ld:R=%lu.%03lu\r\n",phase,(unsigned long)vbus_mv,(unsigned long)v_applied_mv,(long)i_ma,(unsigned long)(r_mohm/1000),(unsigned long)(r_mohm%1000));
    USART2_SendString(buf);
}

int main(void) {
    USART2_Init(); USART2_SendString("Step 1: UART OK\r\n");
    GPIO_Init(); USART2_SendString("Step 2: GPIO OK\r\n");
    ADC2_Init(); USART2_SendString("Step 3: ADC2 OK\r\n");
    TIM1_Init(); TIM8_Init(); USART2_SendString("Step 4: TIM OK\r\n");
    Show_Menu();
    while(1){char cmd=USART2_ReceiveChar();if(cmd=='\r'||cmd=='\n')continue;
        if(cmd=='Q'||cmd=='q'){Measure_Phase('U');delay_volatile(DELAY_1S);Measure_Phase('V');delay_volatile(DELAY_1S);Measure_Phase('W');delay_volatile(DELAY_1S);Show_Menu();continue;}
        Execute_Command(cmd);Show_Menu();}
}
