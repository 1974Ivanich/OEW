#ifndef ADC_H
#define ADC_H

#include <stdint.h>

/* Конфигурация АЦП для OEW FOC */
#define ADC_VREF_MV         3300U
#define ADC_MAX_CODE         4095U

/* Current sensing (STEVAL-IPM20B):
 * PA0 = I1  — ADC2_IN1, фазный ток A (FOC Clarke), шунт 0.03 Ом + ОУ 2.1х, смещение 1.65В
 * PA1 = I2  — ADC2_IN2, фазный ток B (FOC Clarke), шунт 0.03 Ом + ОУ 2.1х, смещение 1.65В
 * PA6 = Ires — ADC2_IN3, трансформатор 1:1000 (Rб=100 Ом), охватывает ВСЕ 3
 *              фазных провода: Ires = iu+iv+iw (нулевая последовательность).
 *              Чувствительность 100 мВ/А, диапазон ±16.5 А (пик).
 *              Используется в FOC: iw = Ires − iu − iv (3-датчиковый Clarke).
 * PC4 = VBUS — ADC2_IN5, напряжение шины (делитель 1:125)
 *
 * Номера каналов сверены с офиц. таблицей пинов STM32G474 (ST PeripheralPins.c).
 * ИСТОРИЯ БАГА: ранее (до фикса) использовались IN7/IN15 — это НЕ PA6/PC4,
 * а PC1 (ШИМ-выход TIM1_CH2!) и PB15 (не настроен, плавающий пин).
 */

/* Коэффициенты пересчёта токов.
 * Чувствительность тракта: Rshunt * Gain = 0.03 * 2.1 = 0.063 В/А = 63 мВ/А.
 * Для диапазона 0-3.3В, смещение 1.65В → ±26.2А.
 * Offset определяется калибровкой (ADC_CalibrateOffsets), не константой. */
#define SHUNT_UV_PER_A      63000UL     /* 0.063 В/А → мкВ/А (Rshunt*Gain) */
#define IRES_UV_PER_A       100000UL    /* трансформатор 1:1000, Rб=100 Ом → 0.1 В/А = 100 мВ/А */

/* Делитель Vbus: 1:125 */
#define VBUS_DIVIDER        125U

/* Количество выборок при калибровке offset */
#define ADC_OFFSET_SAMPLES  256

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
uint16_t ADC_GetRawIres(void);
uint16_t ADC_GetRawVbus(void);

/* Получить физические величины */
int32_t  ADC_GetI1_mA(void);    // фазный ток A, мА (FOC Clarke)
int32_t  ADC_GetI2_mA(void);    // фазный ток B, мА (FOC Clarke)
int32_t  ADC_GetIres_mA(void);  // суммарный ток A+B+C (трансформатор), мА — участвует в FOC: iw = Ires−iu−iv
int32_t  ADC_GetVbus_mV(void);  // напряжение шины, мВ

/* Получить offset (нулевой код) */
uint16_t ADC_GetOffsetI1(void);
uint16_t ADC_GetOffsetI2(void);
uint16_t ADC_GetOffsetIres(void);

/* Калибровка offset для debug tool (256 выборок, все 3 токовых канала) */
void ADC_CalibrateOffsets_256(void);

/* Счётчики диагностики ADC */
uint32_t ADC_GetOvrCount(void);      /* overrun — потерянные измерения */
uint32_t ADC_GetJeosCount(void);     /* успешные JEOS (FOC-циклы) */
uint32_t ADC_GetTimeoutCount(void);  /* таймауты ADC */
uint32_t ADC_GetJqovfCount(void);   /* переполнение injected queue (JQOVF) */

#endif
