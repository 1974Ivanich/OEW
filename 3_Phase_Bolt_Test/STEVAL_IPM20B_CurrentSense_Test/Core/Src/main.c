/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Minimal current sense test for STEVAL-IPM20B (dual inverter)
  * @version        : 1.0
  ******************************************************************************
  */
/* USER CODE END Header */

/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* USER CODE BEGIN PV */

#define PWM_PERIOD          8500U
#define HALF_PERIOD         (PWM_PERIOD / 2U)
#define EXPECTED_ISR_HZ     10000.0f

/* --- Current sensor state --- */
typedef struct {
    float off_a, off_b, off_c;
    float scale_a, scale_b, scale_c;
    uint8_t calibrated;
} CurrentMeas_t;

/* Начальные scale-факторы — приближенные, уточняются экспериментально */
CurrentMeas_t g_curr  = { .scale_a = -0.00035f, .scale_b = -0.00035f, .scale_c =  0.00035f };
CurrentMeas_t g_curr2 = { .scale_a =  0.00035f, .scale_b =  0.00035f, .scale_c =  0.00035f };

volatile uint16_t last_raw_a1 = 0, last_raw_b1 = 0, last_raw_c1 = 0;
volatile uint16_t last_raw_a2 = 0, last_raw_b2 = 0, last_raw_c2 = 0;

/* --- DC test state --- */
static volatile int16_t g_dc_duty = 0;          /* offset от HALF_PERIOD */
static volatile uint8_t g_dc_phase = 0;         /* 0=A, 1=B, 2=C */
static volatile uint8_t g_dc_active = 0;

/* --- ADC continuous print --- */
static volatile uint8_t g_adc_cont = 0;
static uint32_t g_last_adc_print = 0;

/* --- UART receive --- */
#define UART_RX_BUF_SIZE 64U
static uint8_t uart_rx_byte = 0U;
static volatile char uart_rx_buf[UART_RX_BUF_SIZE];
static volatile uint16_t uart_rx_len = 0U;
static volatile uint8_t  uart_rx_ready = 0U;

/* USER CODE END PV */

/* Private variables ---------------------------------------------------------*/
ADC_HandleTypeDef hadc1;
ADC_HandleTypeDef hadc2;
TIM_HandleTypeDef htim1;
TIM_HandleTypeDef htim8;
UART_HandleTypeDef huart2;

/* USER CODE BEGIN 0 */

/* retarget printf to UART2 (HAL blocking for simplicity in this test) */
int _write(int file, char *ptr, int len) {
    (void)file;
    HAL_UART_Transmit(&huart2, (uint8_t*)ptr, (uint16_t)len, HAL_MAX_DELAY);
    return len;
}

static void uart_start_rx(void) {
    HAL_UART_Receive_IT(&huart2, &uart_rx_byte, 1U);
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart) {
    if (huart != &huart2) return;
    uint8_t b = uart_rx_byte;
    if (b == '\r' || b == '\n') {
        if (uart_rx_len > 0 && uart_rx_len < UART_RX_BUF_SIZE) {
            uart_rx_buf[uart_rx_len] = '\0';
            uart_rx_ready = 1U;
        }
        uart_rx_len = 0U;
    } else if (uart_rx_len + 1U < UART_RX_BUF_SIZE) {
        uart_rx_buf[uart_rx_len++] = (char)b;
    }
    uart_start_rx();
}

static void read_raw_adc(void) {
    last_raw_a1 = (uint16_t)hadc1.Instance->JDR1;
    last_raw_b1 = (uint16_t)hadc1.Instance->JDR2;
    last_raw_c1 = (uint16_t)hadc1.Instance->JDR3;
    last_raw_a2 = (uint16_t)hadc2.Instance->JDR1;
    last_raw_b2 = (uint16_t)hadc2.Instance->JDR2;
    last_raw_c2 = (uint16_t)hadc2.Instance->JDR3;
}

static void update_currents(void) {
    g_curr.ia  = g_curr.scale_a  * ((float)last_raw_a1 - g_curr.off_a);
    g_curr.ib  = g_curr.scale_b  * ((float)last_raw_b1 - g_curr.off_b);
    g_curr.ic  = g_curr.scale_c  * ((float)last_raw_c1 - g_curr.off_c);
    g_curr2.ia = g_curr2.scale_a * ((float)last_raw_a2 - g_curr2.off_a);
    g_curr2.ib = g_curr2.scale_b * ((float)last_raw_b2 - g_curr2.off_b);
    g_curr2.ic = g_curr2.scale_c * ((float)last_raw_c2 - g_curr2.off_c);
}

static void print_raw_adc(void) {
    read_raw_adc();
    printf("RAW,"
           "A1=%u,B1=%u,C1=%u,"
           "A2=%u,B2=%u,C2=%u,"
           "cal1=%u,cal2=%u\r\n",
           last_raw_a1, last_raw_b1, last_raw_c1,
           last_raw_a2, last_raw_b2, last_raw_c2,
           (unsigned)g_curr.calibrated, (unsigned)g_curr2.calibrated);
}

static void print_currents(void) {
    read_raw_adc();
    update_currents();
    printf("I,"
           "ia1=%.4f,ib1=%.4f,ic1=%.4f,"
           "ia2=%.4f,ib2=%.4f,ic2=%.4f\r\n",
           g_curr.ia, g_curr.ib, g_curr.ic,
           g_curr2.ia, g_curr2.ib, g_curr2.ic);
}

static void set_all_pwm_half(void) {
    TIM1->CCR1 = HALF_PERIOD; TIM1->CCR2 = HALF_PERIOD; TIM1->CCR3 = HALF_PERIOD;
    TIM8->CCR1 = HALF_PERIOD; TIM8->CCR2 = HALF_PERIOD; TIM8->CCR3 = HALF_PERIOD;
}

static void stop_dc_test(void) {
    g_dc_active = 0;
    g_dc_duty = 0;
    set_all_pwm_half();
    printf("STOPPED\r\n");
}

static void start_dc_test(char phase, int16_t duty_offset) {
    if (duty_offset == 0) {
        stop_dc_test();
        return;
    }
    if (abs(duty_offset) > (int16_t)HALF_PERIOD) {
        printf("ERROR: duty abs must be <= %u\r\n", (unsigned)HALF_PERIOD);
        return;
    }
    g_dc_phase = (phase == 'B') ? 1U : (phase == 'C') ? 2U : 0U;
    g_dc_duty = duty_offset;
    g_dc_active = 1U;
    printf("DC phase=%c duty_offset=%d\r\n", phase, (int)duty_offset);
}

/* Accumulators for calibration */
static void calibrate_offsets(void) {
    stop_dc_test();
    printf("CALIB: averaging 1000 samples, keep current = 0\r\n");
    HAL_Delay(50);

    float sa1 = 0.0f, sb1 = 0.0f, sc1 = 0.0f;
    float sa2 = 0.0f, sb2 = 0.0f, sc2 = 0.0f;
    for (uint16_t i = 0; i < 1000U; i++) {
        read_raw_adc();
        sa1 += (float)last_raw_a1; sb1 += (float)last_raw_b1; sc1 += (float)last_raw_c1;
        sa2 += (float)last_raw_a2; sb2 += (float)last_raw_b2; sc2 += (float)last_raw_c2;
        HAL_Delay(1);
    }
    g_curr.off_a  = sa1 / 1000.0f; g_curr.off_b  = sb1 / 1000.0f; g_curr.off_c  = sc1 / 1000.0f;
    g_curr2.off_a = sa2 / 1000.0f; g_curr2.off_b = sb2 / 1000.0f; g_curr2.off_c = sc2 / 1000.0f;
    g_curr.calibrated = 1U;
    g_curr2.calibrated = 1U;
    printf("CALIB DONE: off_a1=%.2f off_b1=%.2f off_c1=%.2f "
           "off_a2=%.2f off_b2=%.2f off_c2=%.2f\r\n",
           g_curr.off_a, g_curr.off_b, g_curr.off_c,
           g_curr2.off_a, g_curr2.off_b, g_curr2.off_c);
}

/* Apply DC pattern inside ISR-safe main-loop tick (not real ISR, just TIM CCR update) */
static void apply_dc_pwm(void) {
    if (!g_dc_active) return;
    uint32_t duty = (uint32_t)((int32_t)HALF_PERIOD + (int32_t)g_dc_duty);
    if (duty > PWM_PERIOD) duty = PWM_PERIOD;
    switch (g_dc_phase) {
        case 0: /* A+ / B- */
            TIM1->CCR1 = duty;        TIM1->CCR2 = PWM_PERIOD - duty; TIM1->CCR3 = HALF_PERIOD;
            TIM8->CCR1 = PWM_PERIOD - duty; TIM8->CCR2 = duty;        TIM8->CCR3 = HALF_PERIOD;
            break;
        case 1: /* B+ / A- */
            TIM1->CCR1 = PWM_PERIOD - duty; TIM1->CCR2 = duty;        TIM1->CCR3 = HALF_PERIOD;
            TIM8->CCR1 = duty;        TIM8->CCR2 = PWM_PERIOD - duty; TIM8->CCR3 = HALF_PERIOD;
            break;
        case 2: /* C+ / A- */
            TIM1->CCR1 = PWM_PERIOD - duty; TIM1->CCR2 = HALF_PERIOD; TIM1->CCR3 = duty;
            TIM8->CCR1 = duty;        TIM8->CCR2 = HALF_PERIOD; TIM8->CCR3 = PWM_PERIOD - duty;
            break;
        default:
            break;
    }
}

static void print_help(void) {
    printf(
        "Commands:\r\n"
        "  adc            print raw ADC once\r\n"
        "  adccont        print raw ADC every 100 ms\r\n"
        "  adcstop        stop continuous ADC print\r\n"
        "  calib          calibrate zero-current offsets\r\n"
        "  i              compute and print currents\r\n"
        "  scaleA <val>   set scale A (A/LSB)\r\n"
        "  scaleB <val>   set scale B\r\n"
        "  scaleC <val>   set scale C\r\n"
        "  dc A <duty>    DC test phase A, duty offset from half\r\n"
        "  dc B <duty>    DC test phase B\r\n"
        "  dc C <duty>    DC test phase C\r\n"
        "  stop           all phases to 50%%\r\n"
        "  help           this message\r\n"
    );
}

static void process_command(const char *cmd) {
    if (cmd == NULL || cmd[0] == '\0') return;

    char buf[UART_RX_BUF_SIZE];
    strncpy(buf, cmd, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';

    char *tok = strtok(buf, " \t");
    if (tok == NULL) return;

    if (strcmp(tok, "adc") == 0) {
        print_raw_adc();
    } else if (strcmp(tok, "adccont") == 0) {
        g_adc_cont = 1;
        printf("ADC continuous ON\r\n");
    } else if (strcmp(tok, "adcstop") == 0) {
        g_adc_cont = 0;
        printf("ADC continuous OFF\r\n");
    } else if (strcmp(tok, "calib") == 0) {
        calibrate_offsets();
    } else if (strcmp(tok, "i") == 0) {
        print_currents();
    } else if (strcmp(tok, "stop") == 0) {
        stop_dc_test();
    } else if (strcmp(tok, "help") == 0) {
        print_help();
    } else if (strcmp(tok, "scaleA") == 0) {
        char *v = strtok(NULL, " \t");
        if (v) { g_curr.scale_a = strtof(v, NULL); g_curr2.scale_a = g_curr.scale_a; printf("scaleA=%.6f\r\n", g_curr.scale_a); }
    } else if (strcmp(tok, "scaleB") == 0) {
        char *v = strtok(NULL, " \t");
        if (v) { g_curr.scale_b = strtof(v, NULL); g_curr2.scale_b = g_curr.scale_b; printf("scaleB=%.6f\r\n", g_curr.scale_b); }
    } else if (strcmp(tok, "scaleC") == 0) {
        char *v = strtok(NULL, " \t");
        if (v) { g_curr.scale_c = strtof(v, NULL); g_curr2.scale_c = g_curr.scale_c; printf("scaleC=%.6f\r\n", g_curr.scale_c); }
    } else if (strcmp(tok, "dc") == 0) {
        char *phase = strtok(NULL, " \t");
        char *duty  = strtok(NULL, " \t");
        if (phase && duty && (phase[0] == 'A' || phase[0] == 'B' || phase[0] == 'C')) {
            start_dc_test(phase[0], (int16_t)atoi(duty));
        } else {
            printf("USAGE: dc A <duty_offset>\r\n");
        }
    } else {
        printf("UNKNOWN: '%s'\r\n", cmd);
    }
}

/* USER CODE END 0 */

int main(void)
{
    HAL_Init();
    SystemClock_Config();
    MX_GPIO_Init();
    MX_ADC1_Init();
    MX_ADC2_Init();
    MX_TIM1_Init();
    MX_TIM8_Init();
    MX_USART2_UART_Init();

    /* USER CODE BEGIN 2 */
    set_all_pwm_half();
    HAL_TIM_PWM_Start(&htim1, TIM_CHANNEL_1);
    HAL_TIM_PWM_Start(&htim1, TIM_CHANNEL_2);
    HAL_TIM_PWM_Start(&htim1, TIM_CHANNEL_3);
    HAL_TIMEx_PWMN_Start(&htim1, TIM_CHANNEL_1);
    HAL_TIMEx_PWMN_Start(&htim1, TIM_CHANNEL_2);
    HAL_TIMEx_PWMN_Start(&htim1, TIM_CHANNEL_3);

    HAL_TIM_PWM_Start(&htim8, TIM_CHANNEL_1);
    HAL_TIM_PWM_Start(&htim8, TIM_CHANNEL_2);
    HAL_TIM_PWM_Start(&htim8, TIM_CHANNEL_3);
    HAL_TIMEx_PWMN_Start(&htim8, TIM_CHANNEL_1);
    HAL_TIMEx_PWMN_Start(&htim8, TIM_CHANNEL_2);
    HAL_TIMEx_PWMN_Start(&htim8, TIM_CHANNEL_3);

    HAL_ADCEx_Calibration_Start(&hadc1, ADC_SINGLE_ENDED);
    HAL_ADCEx_Calibration_Start(&hadc2, ADC_SINGLE_ENDED);

    HAL_ADCEx_InjectedStart_IT(&hadc1);
    HAL_ADCEx_InjectedStart_IT(&hadc2);

    uart_start_rx();

    printf("\r\nSTEVAL-IPM20B Current Sense Test\r\n");
    printf("Type 'help' for commands\r\n");
    /* USER CODE END 2 */

    while (1)
    {
        /* Process incoming UART command */
        if (uart_rx_ready) {
            char cmd_copy[UART_RX_BUF_SIZE];
            uint32_t primask = __get_PRIMASK();
            __disable_irq();
            memcpy(cmd_copy, (const void*)uart_rx_buf, uart_rx_len + 1U);
            uart_rx_len = 0U;
            uart_rx_ready = 0U;
            __set_PRIMASK(primask);
            process_command(cmd_copy);
        }

        /* Apply DC PWM if active */
        apply_dc_pwm();

        /* Continuous ADC print at ~10 Hz */
        if (g_adc_cont && (HAL_GetTick() - g_last_adc_print >= 100U)) {
            g_last_adc_print = HAL_GetTick();
            print_raw_adc();
        }
    }
}

/* The following stubs are normally generated by CubeMX.
   In a fresh CubeMX project these functions will be auto-generated.
   Here we provide minimal placeholders so the file compiles standalone
   only if you copy these generated functions from your CubeMX output. */

void SystemClock_Config(void) {
    /* Replace with generated code */
    Error_Handler();
}

static void MX_GPIO_Init(void) { }
static void MX_ADC1_Init(void) { }
static void MX_ADC2_Init(void) { }
static void MX_TIM1_Init(void) { }
static void MX_TIM8_Init(void) { }
static void MX_USART2_UART_Init(void) { }

void Error_Handler(void) {
    __disable_irq();
    while (1) { }
}

/* USER CODE BEGIN 4 */

/* USER CODE END 4 */
