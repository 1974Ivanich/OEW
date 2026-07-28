#ifndef ADC_H
#define ADC_H

#include <stdint.h>

/* Конфигурация АЦП для OEW FOC */
#define ADC_VREF_MV         3300U
#define ADC_MAX_CODE         4095U

/* One-shunt конфигурация:
 * PA0 = I1  — общий ток инвертора 1 (STEVAL-IPM20B: шунт 0.03 Ом + ОУ 2.1х, смещение 1.65В)
 * PA1 = I2  — общий ток инвертора 2 (STEVAL-IPM20B: шунт 0.03 Ом + ОУ 2.1х, смещение 1.65В)
 * PA6 = IN  — ток нулевой (трансформаторный датчик, данные TBD)
 * PC4 = VBUS — напряжение шины (делитель 1:125)
 */

/* Коэффициенты пересчёта токов */
/* STEVAL-IPM20B: Rshunt=0.03Ω, Gain=2.1, V_offset=1.65В */
/* Чувствительность: 0.03 * 2.1 = 0.063 В/А = 63 мВ/А */
/* Для диапазона 0-3.3В, смещение 1.65В → ±26.2А */
#define SHUNT_R_OHM         30000UL     // 0.03 Ом → мкОм для точности
#define SHUNT_GAIN          21          // 2.1 × 10
#define SHUNT_UV_PER_A      63000UL     // 0.063 В/А → мкВ/А
#define SHUNT_V_OFFSET_MV   1650U       // смещение 1.65В

/* Трансформаторный датчик (заглушка, уточнить!) */
#define NCT_UV_PER_A        50000UL     // 50 мВ/А (предположительно)
#define NCT_V_OFFSET_MV     1650U       // предположительно Vcc/2

/* Делитель Vbus: 1:125 */
#define VBUS_DIVIDER        125U

void ADC_Init(void);
void ADC_StartConversion(void);   /* regular group — для калибровки/телеметрии */
void ADC_CalibrateOffsets(void);  /* калибровка нулей токов (инвертор выключен!) */
void ADC_WaitForEOC(void);

/* Injected group — аппаратный запуск от TIM1_TRGO, ISR → FOC_Run */
void ADC_InjectedInit(void);      /* конфигурация JSQR, прерывание JEOS */
void ADC_InjectedStart(void);     /* JADSTART — ожидание триггера от TIM1 */
void ADC_InjectedStop(void);      /* JADSTP — остановка injected group */
void ADC_ReadInjected(void);      /* чтение JDR1-4 → обновление adc_data (из ISR) */

/* Получить сырые коды АЦП */
uint16_t ADC_GetRawI1(void);
uint16_t ADC_GetRawI2(void);
uint16_t ADC_GetRawIN(void);
uint16_t ADC_GetRawVbus(void);

/* Получить физические величины */
int32_t  ADC_GetI1_mA(void);    // ток инвертора 1, мА
int32_t  ADC_GetI2_mA(void);    // ток инвертора 2, мА
int32_t  ADC_GetIN_mA(void);    // нулевой ток, мА
int32_t  ADC_GetVbus_mV(void);  // напряжение шины, мВ

/* Получить offset (нулевой код) */
uint16_t ADC_GetOffsetI1(void);
uint16_t ADC_GetOffsetI2(void);
uint16_t ADC_GetOffsetIN(void);
uint16_t ADC_GetOffset(void);

/* Калибровка offset для debug tool (256 выборок) */
void ADC_CalibrateI1_256(void);

#endif
