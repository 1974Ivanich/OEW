/* USER CODE BEGIN Header */
/**
******************************************************************************
* @file           : main.c
* @brief          : Ultimate OEW Motor Control (FOC Only + ZSC PI + Field Weakening + OEW Phase Shift)
* @version        : 2.0 (Pure FOC)
******************************************************************************
*/
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

__IO uint32_t BspButtonState = BUTTON_RELEASED;
ADC_HandleTypeDef hadc1;
ADC_HandleTypeDef hadc2;

CORDIC_HandleTypeDef hcordic;

TIM_HandleTypeDef htim1;
TIM_HandleTypeDef htim8;

UART_HandleTypeDef huart2;

/* USER CODE BEGIN PV */
#define PWM_PERIOD          8500U
#define HALF_PERIOD         (PWM_PERIOD / 2U)
#define DC_TEST_TIMEOUT_MS  15000U

/* --- DC-link shunt / ZSC sensor state --- */
typedef struct {
    uint16_t raw;
    float value;
    float offset;
    float scale;
    uint8_t calibrated;
} DcShunt_t;

/* --- Reconstructed phase currents --- */
typedef struct {
    float ia, ib, ic;                /* computed phase currents [A] */
    float off_a, off_b, off_c;       /* zero-current offsets        */
    float scale_a, scale_b, scale_c; /* ADC -> Ampere scale factors */
    uint8_t calibrated;
} CurrentMeas_t;

DcShunt_t g_dc1 = { .scale = 0.0000875f };
DcShunt_t g_dc2 = { .scale = 0.0000875f };
DcShunt_t g_zsc = { .scale = 0.0000875f };

CurrentMeas_t g_inv1 = { .scale_a = -0.0000875f, .scale_b = -0.0000875f, .scale_c =  0.0000875f };
CurrentMeas_t g_inv2 = { .scale_a =  0.0000875f, .scale_b =  0.0000875f, .scale_c =  0.0000875f };

volatile uint16_t last_raw_dc1 = 0, last_raw_izs = 0, last_raw_dc2 = 0;

/* --- DC test state --- */
static volatile int16_t g_dc_duty = 0;          /* offset от HALF_PERIOD */
static volatile uint8_t g_dc_phase = 0;         /* 0=A, 1=B, 2=C */
static volatile uint8_t g_dc_active = 0;
static uint32_t g_dc_started_at = 0U;

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

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_ADC1_Init(void);
static void MX_ADC2_Init(void);
static void MX_TIM1_Init(void);
static void MX_TIM8_Init(void);
static void MX_CORDIC_Init(void);
static void MX_USART2_UART_Init(void);
/* USER CODE BEGIN PFP */

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
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
        if (uart_rx_len > 0) {
            if (uart_rx_len >= UART_RX_BUF_SIZE) {
                uart_rx_len = UART_RX_BUF_SIZE - 1; // Защита от переполнения
            }
            uart_rx_buf[uart_rx_len] = '\0'; // Гарантированно ставим \0
            uart_rx_ready = 1U;
        }
        uart_rx_len = 0U;
    } else {
        if (uart_rx_len < UART_RX_BUF_SIZE - 1) { // Строгое ограничение
            uart_rx_buf[uart_rx_len++] = (char)b;
        }
    }
    uart_start_rx();
}

/* Single-shunt current reconstruction helpers.
 * This is a test/demo implementation. For full FOC it must be called
 * from the ISR after the ADC samples are captured at the mid-points
 * of the two active vectors in each half PWM period.
 */

/* Active vector (Sa,Sb,Sc) -> which phase current flows in DC shunt and sign.
 * Two active vectors per SVPWM sector. The coefficients are used as:
 * i_phase = sign * i_dc, where sign is +1 or -1.
 */
static const int8_t shunt_vector_table[6][2][3] = {
    /* Sector 0: U4(100)=+ia, U6(110)=-ic */
    {{ 1,  0,  0 }, { 0,  0, -1 }},
    /* Sector 1: U2(010)=+ib, U6(110)=-ic */
    {{ 0,  1,  0 }, { 0,  0, -1 }},
    /* Sector 2: U2(010)=+ib, U3(011)=-ia */
    {{ 0,  1,  0 }, { -1, 0,  0 }},
    /* Sector 3: U1(001)=+ic, U3(011)=-ia */
    {{ 0,  0,  1 }, { -1, 0,  0 }},
    /* Sector 4: U1(001)=+ic, U5(101)=-ib */
    {{ 0,  0,  1 }, { 0, -1,  0 }},
    /* Sector 5: U4(100)=+ia, U5(101)=-ib */
    {{ 1,  0,  0 }, { 0, -1,  0 }}
};

/* Reconstruct phase currents from two DC-link samples and known sector.
 * For the test program this is called manually; in FOC it is called from ISR.
 * s1/s2 are DC-link shunt currents in Amperes (after offset/scale).
 * If a sample is invalid (too short vector) it should be passed as NaN or 0
 * and the corresponding phase current is held.
 */
static void shunt_reconstruct(uint8_t sector, float s1, float s2, CurrentMeas_t *out)
{
    if (sector > 5) sector = 0;

    const int8_t *v1 = shunt_vector_table[sector][0];
    const int8_t *v2 = shunt_vector_table[sector][1];

    float i_a = 0.0f, i_b = 0.0f, i_c = 0.0f;

    if (!isnan(s1)) {
        if (v1[0]) i_a = v1[0] * s1;
        if (v1[1]) i_b = v1[1] * s1;
        if (v1[2]) i_c = v1[2] * s1;
    }
    if (!isnan(s2)) {
        if (v2[0]) i_a = v2[0] * s2;
        if (v2[1]) i_b = v2[1] * s2;
        if (v2[2]) i_c = v2[2] * s2;
    }

    /* For OEW: the reconstructed shunt currents include common mode.
     * The actual phase currents need subtraction of izs/3 per inverter.
     * In this test program izs correction is applied in update_currents().
     */
    out->ia = i_a;
    out->ib = i_b;
    out->ic = i_c;
}

static void read_raw_adc(void) {
    last_raw_dc1 = (uint16_t)hadc1.Instance->JDR1;
    last_raw_izs = (uint16_t)hadc1.Instance->JDR2;
    last_raw_dc2 = (uint16_t)hadc2.Instance->JDR1;
}

static void update_currents(void) {
    g_dc1.raw = last_raw_dc1;
    g_dc2.raw = last_raw_dc2;
    g_zsc.raw = last_raw_izs;

    g_dc1.value = g_dc1.scale * ((float)last_raw_dc1 - g_dc1.offset);
    g_dc2.value = g_dc2.scale * ((float)last_raw_dc2 - g_dc2.offset);
    g_zsc.value = g_zsc.scale * ((float)last_raw_izs  - g_zsc.offset);

    /* For test program: reconstruct from a single DC sample if DC test is active.
     * In FOC this is replaced by sector from SVPWM and two samples per half period. */
    uint8_t sec1 = 0, sec2 = 0;
    if (g_dc_active) {
        /* DC test: only one phase active, sector maps directly to the active vector */
        switch (g_dc_phase) {
            case 0: sec1 = 0; break; /* phase A -> U4 (+ia) */
            case 1: sec1 = 2; break; /* phase B -> U2 (+ib) */
            case 2: sec1 = 3; break; /* phase C -> U1 (+ic) */
        }
        sec2 = sec1; /* for inverter 2 DC test, sign may be inverted; adjust by scale sign */
    }

    shunt_reconstruct(sec1, g_dc1.value, NAN, &g_inv1);
    shunt_reconstruct(sec2, g_dc2.value, NAN, &g_inv2);

    /* OEW correction: subtract common ZSC component.
     * izs is measured by g_zsc. The sum of reconstructed phase currents of each
     * inverter contains the zero-sequence component. Subtract izs/3 from each
     * phase to get the actual phase currents.
     * Note: the sign convention of g_zsc must be checked experimentally.
     */
    float zsc = g_zsc.value;
    g_inv1.ia -= zsc / 3.0f;
    g_inv1.ib -= zsc / 3.0f;
    g_inv1.ic -= zsc / 3.0f;
    g_inv2.ia += zsc / 3.0f;  /* opposite sign for second inverter */
    g_inv2.ib += zsc / 3.0f;
    g_inv2.ic += zsc / 3.0f;
}

static void print_raw_adc(void) {
    read_raw_adc();
    printf("RAW,dc1=%u,izs=%u,dc2=%u,cal_dc1=%u,cal_izs=%u,cal_dc2=%u\r\n",
           last_raw_dc1, last_raw_izs, last_raw_dc2,
           (unsigned)g_dc1.calibrated, (unsigned)g_zsc.calibrated, (unsigned)g_dc2.calibrated);
}

static void print_currents(void) {
    read_raw_adc();
    update_currents();
    printf("I,ia1=%.4f,ib1=%.4f,ic1=%.4f,ia2=%.4f,ib2=%.4f,ic2=%.4f,izs=%.4f\r\n",
           g_inv1.ia, g_inv1.ib, g_inv1.ic,
           g_inv2.ia, g_inv2.ib, g_inv2.ic,
           g_zsc.value);
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

static void check_dc_test_timeout(void) {
    if (g_dc_active && (HAL_GetTick() - g_dc_started_at >= DC_TEST_TIMEOUT_MS)) {
        stop_dc_test();
        printf("DC TIMEOUT\r\n");
    }
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
    g_dc_started_at = HAL_GetTick();
    g_dc_active = 1U;
    printf("DC phase=%c duty_offset=%d\r\n", phase, (int)duty_offset);
}

/* Accumulators for calibration */
static void calibrate_offsets(void) {
    stop_dc_test();
    printf("CALIB: averaging 1000 samples, keep current = 0\r\n");
    HAL_Delay(50);

    float sdc1 = 0.0f, sizs = 0.0f, sdc2 = 0.0f;
    for (uint16_t i = 0; i < 1000U; i++) {
        read_raw_adc();
        sdc1 += (float)last_raw_dc1;
        sizs += (float)last_raw_izs;
        sdc2 += (float)last_raw_dc2;
        HAL_Delay(1);
    }
    g_dc1.offset = sdc1 / 1000.0f;
    g_zsc.offset = sizs / 1000.0f;
    g_dc2.offset = sdc2 / 1000.0f;

    g_inv1.off_a = g_inv1.off_b = g_inv1.off_c = g_dc1.offset;
    g_inv2.off_a = g_inv2.off_b = g_inv2.off_c = g_dc2.offset;

    g_dc1.calibrated = 1U;
    g_zsc.calibrated = 1U;
    g_dc2.calibrated = 1U;
    g_inv1.calibrated = 1U;
    g_inv2.calibrated = 1U;
    printf("CALIB DONE: off_dc1=%.2f off_izs=%.2f off_dc2=%.2f\r\n",
           g_dc1.offset, g_zsc.offset, g_dc2.offset);
}

/* Apply DC pattern inside ISR-safe main-loop tick (not real ISR, just TIM CCR update)
 *
 * Motor phases have independent (free) ends:
 *   A: start -> TIM1_CH1, end -> TIM8_CH1
 *   B: start -> TIM1_CH2, end -> TIM8_CH2
 *   C: start -> TIM1_CH3, end -> TIM8_CH3
 *
 * For a selected phase, drive current through that single phase only.
 * Positive duty -> start tied to VDC+, end to VDC-.
 * Negative duty inverts polarity automatically because duty < HALF_PERIOD.
 */
static void apply_dc_pwm(void) {
    if (!g_dc_active) return;
    uint32_t duty = (uint32_t)((int32_t)HALF_PERIOD + (int32_t)g_dc_duty);
    if (duty > PWM_PERIOD) duty = PWM_PERIOD;
    uint32_t inv = PWM_PERIOD - duty;
    switch (g_dc_phase) {
        case 0: /* Phase A only */
            TIM1->CCR1 = duty;        TIM1->CCR2 = HALF_PERIOD; TIM1->CCR3 = HALF_PERIOD;
            TIM8->CCR1 = inv;         TIM8->CCR2 = HALF_PERIOD; TIM8->CCR3 = HALF_PERIOD;
            break;
        case 1: /* Phase B only */
            TIM1->CCR1 = HALF_PERIOD; TIM1->CCR2 = duty;         TIM1->CCR3 = HALF_PERIOD;
            TIM8->CCR1 = HALF_PERIOD; TIM8->CCR2 = inv;         TIM8->CCR3 = HALF_PERIOD;
            break;
        case 2: /* Phase C only */
            TIM1->CCR1 = HALF_PERIOD; TIM1->CCR2 = HALF_PERIOD; TIM1->CCR3 = duty;
            TIM8->CCR1 = HALF_PERIOD; TIM8->CCR2 = HALF_PERIOD; TIM8->CCR3 = inv;
            break;
        default:
            break;
    }
}

static void print_help(void) {
    printf(
        "Commands:\r\n"
        "  adc            print raw ADC once (dc1, izs, dc2)\r\n"
        "  adccont        print raw ADC every 100 ms\r\n"
        "  adcstop        stop continuous ADC print\r\n"
        "  calib          calibrate zero-current offsets\r\n"
        "  i              compute and print reconstructed currents\r\n"
        "  scale1 <val>   set scale for DC shunt of inverter 1 (A/LSB)\r\n"
        "  scale2 <val>   set scale for DC shunt of inverter 2\r\n"
        "  scalez <val>   set scale for ZSC sensor\r\n"
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
        printf("ADC mapping: dc1=ADC1_JDR1, izs=ADC1_JDR2, dc2=ADC2_JDR1\r\n");
        print_raw_adc();
    } else if (strcmp(tok, "adccont") == 0) {
        g_adc_cont = 1;
        printf("ADC continuous ON (dc1, izs, dc2)\r\n");
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
    } else if (strcmp(tok, "scale1") == 0) {
        char *v = strtok(NULL, " \t");
        if (v) { g_dc1.scale = strtof(v, NULL); printf("scale1=%.6f\r\n", g_dc1.scale); }
    } else if (strcmp(tok, "scale2") == 0) {
        char *v = strtok(NULL, " \t");
        if (v) { g_dc2.scale = strtof(v, NULL); printf("scale2=%.6f\r\n", g_dc2.scale); }
    } else if (strcmp(tok, "scalez") == 0) {
        char *v = strtok(NULL, " \t");
        if (v) { g_zsc.scale = strtof(v, NULL); printf("scalez=%.6f\r\n", g_zsc.scale); }
    } else if (strcmp(tok, "dc") == 0) {
        char *phase = strtok(NULL, " \t");
        char *duty  = strtok(NULL, " \t");
        if (phase && duty && (phase[0] == 'A' || phase[0] == 'B' || phase[0] == 'C')) {
            start_dc_test(phase[0], (int16_t)atoi(duty));
        } else {
            printf("USAGE: dc A <duty_offset>\r\n");
        }
    } else {
    	printf("UNKNOWN: '%s'\r\n", buf);
    }
}
/* USER CODE END 0 */

/**
  * @brief  The application entry point.
  * @retval int
  */
int main(void)
{

  /* USER CODE BEGIN 1 */

  /* USER CODE END 1 */

  /* MCU Configuration--------------------------------------------------------*/

  /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
  HAL_Init();

  /* USER CODE BEGIN Init */

  /* USER CODE END Init */

  /* Configure the system clock */
  SystemClock_Config();

  /* USER CODE BEGIN SysInit */

  /* USER CODE END SysInit */

  /* Initialize all configured peripherals */
  MX_GPIO_Init();
  MX_ADC1_Init();
  MX_ADC2_Init();
  MX_TIM1_Init();
  MX_TIM8_Init();
  MX_CORDIC_Init();
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

    // 1. Калибровка ADC
    HAL_ADCEx_Calibration_Start(&hadc1, ADC_SINGLE_ENDED);
    HAL_ADCEx_Calibration_Start(&hadc2, ADC_SINGLE_ENDED);

    // 2. Запуск ADC2 (Ведомый, БЕЗ прерываний!)
    HAL_ADCEx_InjectedStart(&hadc2);

    // 3. Запуск ADC1 (Ведущий, С прерываниями)
    HAL_ADCEx_InjectedStart_IT(&hadc1);


    uart_start_rx();

    printf("\r\nSTEVAL-IPM20B Current Sense Test\r\n");
    printf("Type 'help' for commands\r\n");
  /* USER CODE END 2 */

  /* Initialize led */
  BSP_LED_Init(LED_GREEN);

  /* Initialize USER push-button, will be used to trigger an interrupt each time it's pressed.*/
  BSP_PB_Init(BUTTON_USER, BUTTON_MODE_EXTI);

  /* USER CODE BEGIN BSP */

  /* -- Sample board code to switch on led ---- */
  BSP_LED_On(LED_GREEN);

  /* USER CODE END BSP */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */

   while (1)
  {

    /* -- Sample board code for User push-button in interrupt mode ---- */
    if (BspButtonState == BUTTON_PRESSED)
    {
      BspButtonState = BUTTON_RELEASED;
      BSP_LED_Toggle(LED_GREEN);
    }
    /* Process incoming UART command */
    if (uart_rx_ready) {
        char cmd_copy[UART_RX_BUF_SIZE];
        uint32_t primask = __get_PRIMASK();
        __disable_irq();
        // БЕЗОПАСНОЕ копирование:
        strncpy(cmd_copy, (const char*)uart_rx_buf, UART_RX_BUF_SIZE - 1);
        cmd_copy[UART_RX_BUF_SIZE - 1] = '\0';
        uart_rx_len = 0U;
        uart_rx_ready = 0U;
        __set_PRIMASK(primask);
        process_command(cmd_copy);
    }

        check_dc_test_timeout();
        /* Apply DC PWM if active */
        apply_dc_pwm();

        /* Continuous ADC print at ~10 Hz */
        if (g_adc_cont && (HAL_GetTick() - g_last_adc_print >= 100U)) {
            g_last_adc_print = HAL_GetTick();
            print_raw_adc();
        }

    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
  }
  /* USER CODE END 3 */
}

/**
  * @brief System Clock Configuration
  * @retval None
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  /** Configure the main internal regulator output voltage
  */
  HAL_PWREx_ControlVoltageScaling(PWR_REGULATOR_VOLTAGE_SCALE1_BOOST);

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState = RCC_HSE_ON;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLM = RCC_PLLM_DIV6;
  RCC_OscInitStruct.PLL.PLLN = 85;
  RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV2;
  RCC_OscInitStruct.PLL.PLLQ = RCC_PLLQ_DIV2;
  RCC_OscInitStruct.PLL.PLLR = RCC_PLLR_DIV2;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV1;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_4) != HAL_OK)
  {
    Error_Handler();
  }
}

/**
  * @brief ADC1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_ADC1_Init(void)
{

  /* USER CODE BEGIN ADC1_Init 0 */

  /* USER CODE END ADC1_Init 0 */

  ADC_MultiModeTypeDef multimode = {0};
  ADC_InjectionConfTypeDef sConfigInjected = {0};

  /* USER CODE BEGIN ADC1_Init 1 */

  /* USER CODE END ADC1_Init 1 */

  /** Common config
  */
  hadc1.Instance = ADC1;
  hadc1.Init.ClockPrescaler = ADC_CLOCK_SYNC_PCLK_DIV4;
  hadc1.Init.Resolution = ADC_RESOLUTION_12B;
  hadc1.Init.DataAlign = ADC_DATAALIGN_RIGHT;
  hadc1.Init.GainCompensation = 0;
  hadc1.Init.ScanConvMode = ADC_SCAN_ENABLE;
  hadc1.Init.EOCSelection = ADC_EOC_SINGLE_CONV;
  hadc1.Init.LowPowerAutoWait = DISABLE;
  hadc1.Init.ContinuousConvMode = DISABLE;
  hadc1.Init.NbrOfConversion = 1;
  hadc1.Init.DiscontinuousConvMode = DISABLE;
  hadc1.Init.DMAContinuousRequests = DISABLE;
  hadc1.Init.Overrun = ADC_OVR_DATA_PRESERVED;
  hadc1.Init.OversamplingMode = DISABLE;
  if (HAL_ADC_Init(&hadc1) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure the ADC multi-mode
  */
  multimode.Mode = ADC_DUALMODE_INJECSIMULT;
  multimode.DMAAccessMode = ADC_DMAACCESSMODE_DISABLED;
  multimode.TwoSamplingDelay = ADC_TWOSAMPLINGDELAY_1CYCLE;
  if (HAL_ADCEx_MultiModeConfigChannel(&hadc1, &multimode) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure Injected Channel
  */
  sConfigInjected.InjectedChannel = ADC_CHANNEL_5;
  sConfigInjected.InjectedRank = ADC_INJECTED_RANK_1;
  sConfigInjected.InjectedSamplingTime = ADC_SAMPLETIME_12CYCLES_5;
  sConfigInjected.InjectedSingleDiff = ADC_SINGLE_ENDED;
  sConfigInjected.InjectedOffsetNumber = ADC_OFFSET_NONE;
  sConfigInjected.InjectedOffset = 0;
  sConfigInjected.InjectedNbrOfConversion = 2;
  sConfigInjected.InjectedDiscontinuousConvMode = DISABLE;
  sConfigInjected.AutoInjectedConv = DISABLE;
  sConfigInjected.QueueInjectedContext = DISABLE;
  sConfigInjected.ExternalTrigInjecConv = ADC_EXTERNALTRIGINJEC_T1_TRGO;
  sConfigInjected.ExternalTrigInjecConvEdge = ADC_EXTERNALTRIGINJECCONV_EDGE_RISING;
  sConfigInjected.InjecOversamplingMode = ENABLE;
  sConfigInjected.InjecOversampling.Ratio = ADC_OVERSAMPLING_RATIO_16;
  sConfigInjected.InjecOversampling.RightBitShift = ADC_RIGHTBITSHIFT_NONE;
  if (HAL_ADCEx_InjectedConfigChannel(&hadc1, &sConfigInjected) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure Injected Channel
  */
  sConfigInjected.InjectedChannel = ADC_CHANNEL_8;
  sConfigInjected.InjectedRank = ADC_INJECTED_RANK_2;
  if (HAL_ADCEx_InjectedConfigChannel(&hadc1, &sConfigInjected) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN ADC1_Init 2 */

  /* USER CODE END ADC1_Init 2 */

}

/**
  * @brief ADC2 Initialization Function
  * @param None
  * @retval None
  */
static void MX_ADC2_Init(void)
{

  /* USER CODE BEGIN ADC2_Init 0 */

  /* USER CODE END ADC2_Init 0 */

  ADC_ChannelConfTypeDef sConfig = {0};
  ADC_InjectionConfTypeDef sConfigInjected = {0};

  /* USER CODE BEGIN ADC2_Init 1 */

  /* USER CODE END ADC2_Init 1 */

  /** Common config
  */
  hadc2.Instance = ADC2;
  hadc2.Init.ClockPrescaler = ADC_CLOCK_SYNC_PCLK_DIV4;
  hadc2.Init.Resolution = ADC_RESOLUTION_12B;
  hadc2.Init.DataAlign = ADC_DATAALIGN_RIGHT;
  hadc2.Init.GainCompensation = 0;
  hadc2.Init.ScanConvMode = ADC_SCAN_DISABLE;
  hadc2.Init.EOCSelection = ADC_EOC_SINGLE_CONV;
  hadc2.Init.LowPowerAutoWait = DISABLE;
  hadc2.Init.ContinuousConvMode = DISABLE;
  hadc2.Init.NbrOfConversion = 1;
  hadc2.Init.DiscontinuousConvMode = DISABLE;
  hadc2.Init.DMAContinuousRequests = DISABLE;
  hadc2.Init.Overrun = ADC_OVR_DATA_PRESERVED;
  hadc2.Init.OversamplingMode = DISABLE;
  if (HAL_ADC_Init(&hadc2) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure Regular Channel
  */
  sConfig.Channel = ADC_CHANNEL_4;
  sConfig.Rank = ADC_REGULAR_RANK_1;
  sConfig.SamplingTime = ADC_SAMPLETIME_12CYCLES_5;
  sConfig.SingleDiff = ADC_SINGLE_ENDED;
  sConfig.OffsetNumber = ADC_OFFSET_NONE;
  sConfig.Offset = 0;
  if (HAL_ADC_ConfigChannel(&hadc2, &sConfig) != HAL_OK)
  {
    Error_Handler();
  }

  /** Configure Injected Channel
  */
  sConfigInjected.InjectedChannel = ADC_CHANNEL_4;
  sConfigInjected.InjectedRank = ADC_INJECTED_RANK_1;
  sConfigInjected.InjectedSamplingTime = ADC_SAMPLETIME_2CYCLES_5;
  sConfigInjected.InjectedSingleDiff = ADC_SINGLE_ENDED;
  sConfigInjected.InjectedOffsetNumber = ADC_OFFSET_NONE;
  sConfigInjected.InjectedOffset = 0;
  sConfigInjected.InjectedNbrOfConversion = 1;
  sConfigInjected.InjectedDiscontinuousConvMode = DISABLE;
  sConfigInjected.AutoInjectedConv = DISABLE;
  sConfigInjected.QueueInjectedContext = DISABLE;
  sConfigInjected.ExternalTrigInjecConv = ADC_EXTERNALTRIGINJEC_T1_TRGO;
  sConfigInjected.ExternalTrigInjecConvEdge = ADC_EXTERNALTRIGINJECCONV_EDGE_RISING;
  sConfigInjected.InjecOversamplingMode = ENABLE;
  sConfigInjected.InjecOversampling.Ratio = ADC_OVERSAMPLING_RATIO_16;
  sConfigInjected.InjecOversampling.RightBitShift = ADC_RIGHTBITSHIFT_NONE;
  if (HAL_ADCEx_InjectedConfigChannel(&hadc2, &sConfigInjected) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN ADC2_Init 2 */

  /* USER CODE END ADC2_Init 2 */

}

/**
  * @brief CORDIC Initialization Function
  * @param None
  * @retval None
  */
static void MX_CORDIC_Init(void)
{

  /* USER CODE BEGIN CORDIC_Init 0 */

  /* USER CODE END CORDIC_Init 0 */

  /* USER CODE BEGIN CORDIC_Init 1 */

  /* USER CODE END CORDIC_Init 1 */
  hcordic.Instance = CORDIC;
  if (HAL_CORDIC_Init(&hcordic) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN CORDIC_Init 2 */

  // Явная конфигурация CORDIC для FOC:
  //   FUNC=0     -> Cosine (CORDIC_Cos напрямую, CORDIC_Sin через сдвиг на pi/2)
  //   PRECISION=24 -> высокая точность (по умолчанию 4 итераций недостаточно для токового контура)
  //   SCALE=0    -> корректно для cosine (выход в q31 [-1, 1])
  //   ARGSIZE=3  -> q31 angle в радианах [-pi, pi) (по умолчанию)
  //   RESSIZE=3  -> q31 output (по умолчанию)
  // Используем MODIFY_REG, чтобы не сбросить прочие поля CSR.
  MODIFY_REG(CORDIC->CSR,
             CORDIC_CSR_FUNC_Msk | CORDIC_CSR_PRECISION_Msk,
             (0U  << CORDIC_CSR_FUNC_Pos)      |  // FUNC = 0: Cosine
             (24U << CORDIC_CSR_PRECISION_Pos));  // PRECISION = 24 итераций

  /* USER CODE END CORDIC_Init 2 */

}

/**
  * @brief TIM1 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM1_Init(void)
{

  /* USER CODE BEGIN TIM1_Init 0 */

  /* USER CODE END TIM1_Init 0 */

  TIM_MasterConfigTypeDef sMasterConfig = {0};
  TIM_OC_InitTypeDef sConfigOC = {0};
  TIM_BreakDeadTimeConfigTypeDef sBreakDeadTimeConfig = {0};

  /* USER CODE BEGIN TIM1_Init 1 */

  /* USER CODE END TIM1_Init 1 */
  htim1.Instance = TIM1;
  htim1.Init.Prescaler = 0;
  htim1.Init.CounterMode = TIM_COUNTERMODE_CENTERALIGNED1;
  htim1.Init.Period = 8499;
  htim1.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim1.Init.RepetitionCounter = 1;
  htim1.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
  if (HAL_TIM_PWM_Init(&htim1) != HAL_OK)
  {
    Error_Handler();
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_UPDATE;
  sMasterConfig.MasterOutputTrigger2 = TIM_TRGO2_RESET;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_ENABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim1, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sConfigOC.OCMode = TIM_OCMODE_PWM1;
  sConfigOC.Pulse = 0;
  sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
  sConfigOC.OCNPolarity = TIM_OCNPOLARITY_HIGH;
  sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;
  sConfigOC.OCIdleState = TIM_OCIDLESTATE_RESET;
  sConfigOC.OCNIdleState = TIM_OCNIDLESTATE_RESET;
  if (HAL_TIM_PWM_ConfigChannel(&htim1, &sConfigOC, TIM_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }
  __HAL_TIM_DISABLE_OCxPRELOAD(&htim1, TIM_CHANNEL_1);
  if (HAL_TIM_PWM_ConfigChannel(&htim1, &sConfigOC, TIM_CHANNEL_3) != HAL_OK)
  {
    Error_Handler();
  }
  __HAL_TIM_DISABLE_OCxPRELOAD(&htim1, TIM_CHANNEL_3);
  sBreakDeadTimeConfig.OffStateRunMode = TIM_OSSR_DISABLE;
  sBreakDeadTimeConfig.OffStateIDLEMode = TIM_OSSI_DISABLE;
  sBreakDeadTimeConfig.LockLevel = TIM_LOCKLEVEL_OFF;
  sBreakDeadTimeConfig.DeadTime = 255;
  sBreakDeadTimeConfig.BreakState = TIM_BREAK_DISABLE;
  sBreakDeadTimeConfig.BreakPolarity = TIM_BREAKPOLARITY_HIGH;
  sBreakDeadTimeConfig.BreakFilter = 0;
  sBreakDeadTimeConfig.BreakAFMode = TIM_BREAK_AFMODE_INPUT;
  sBreakDeadTimeConfig.Break2State = TIM_BREAK2_DISABLE;
  sBreakDeadTimeConfig.Break2Polarity = TIM_BREAK2POLARITY_HIGH;
  sBreakDeadTimeConfig.Break2Filter = 0;
  sBreakDeadTimeConfig.Break2AFMode = TIM_BREAK_AFMODE_INPUT;
  sBreakDeadTimeConfig.AutomaticOutput = TIM_AUTOMATICOUTPUT_DISABLE;
  if (HAL_TIMEx_ConfigBreakDeadTime(&htim1, &sBreakDeadTimeConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN TIM1_Init 2 */

  /* USER CODE END TIM1_Init 2 */
  HAL_TIM_MspPostInit(&htim1);

}

/**
  * @brief TIM8 Initialization Function
  * @param None
  * @retval None
  */
static void MX_TIM8_Init(void)
{

  /* USER CODE BEGIN TIM8_Init 0 */

  /* USER CODE END TIM8_Init 0 */

  TIM_SlaveConfigTypeDef sSlaveConfig = {0};
  TIM_MasterConfigTypeDef sMasterConfig = {0};
  TIM_OC_InitTypeDef sConfigOC = {0};
  TIM_BreakDeadTimeConfigTypeDef sBreakDeadTimeConfig = {0};

  /* USER CODE BEGIN TIM8_Init 1 */

  /* USER CODE END TIM8_Init 1 */
  htim8.Instance = TIM8;
  htim8.Init.Prescaler = 0;
  htim8.Init.CounterMode = TIM_COUNTERMODE_CENTERALIGNED1;
  htim8.Init.Period = 8499;
  htim8.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim8.Init.RepetitionCounter = 0;
  htim8.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
  if (HAL_TIM_Base_Init(&htim8) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_Init(&htim8) != HAL_OK)
  {
    Error_Handler();
  }
  sSlaveConfig.SlaveMode = TIM_SLAVEMODE_TRIGGER;
  sSlaveConfig.InputTrigger = TIM_TS_ITR0;
  if (HAL_TIM_SlaveConfigSynchro(&htim8, &sSlaveConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_RESET;
  sMasterConfig.MasterOutputTrigger2 = TIM_TRGO2_RESET;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim8, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sConfigOC.OCMode = TIM_OCMODE_PWM1;
  sConfigOC.Pulse = 0;
  sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
  sConfigOC.OCNPolarity = TIM_OCNPOLARITY_HIGH;
  sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;
  sConfigOC.OCIdleState = TIM_OCIDLESTATE_RESET;
  sConfigOC.OCNIdleState = TIM_OCNIDLESTATE_RESET;
  if (HAL_TIM_PWM_ConfigChannel(&htim8, &sConfigOC, TIM_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_ConfigChannel(&htim8, &sConfigOC, TIM_CHANNEL_2) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_TIM_PWM_ConfigChannel(&htim8, &sConfigOC, TIM_CHANNEL_3) != HAL_OK)
  {
    Error_Handler();
  }
  sBreakDeadTimeConfig.OffStateRunMode = TIM_OSSR_DISABLE;
  sBreakDeadTimeConfig.OffStateIDLEMode = TIM_OSSI_DISABLE;
  sBreakDeadTimeConfig.LockLevel = TIM_LOCKLEVEL_OFF;
  sBreakDeadTimeConfig.DeadTime = 255;
  sBreakDeadTimeConfig.BreakState = TIM_BREAK_DISABLE;
  sBreakDeadTimeConfig.BreakPolarity = TIM_BREAKPOLARITY_HIGH;
  sBreakDeadTimeConfig.BreakFilter = 0;
  sBreakDeadTimeConfig.BreakAFMode = TIM_BREAK_AFMODE_INPUT;
  sBreakDeadTimeConfig.Break2State = TIM_BREAK2_DISABLE;
  sBreakDeadTimeConfig.Break2Polarity = TIM_BREAK2POLARITY_HIGH;
  sBreakDeadTimeConfig.Break2Filter = 0;
  sBreakDeadTimeConfig.Break2AFMode = TIM_BREAK_AFMODE_INPUT;
  sBreakDeadTimeConfig.AutomaticOutput = TIM_AUTOMATICOUTPUT_DISABLE;
  if (HAL_TIMEx_ConfigBreakDeadTime(&htim8, &sBreakDeadTimeConfig) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN TIM8_Init 2 */

  /* USER CODE END TIM8_Init 2 */
  HAL_TIM_MspPostInit(&htim8);

}

/**
  * @brief USART2 Initialization Function
  * @param None
  * @retval None
  */
static void MX_USART2_UART_Init(void)
{

  /* USER CODE BEGIN USART2_Init 0 */

  /* USER CODE END USART2_Init 0 */

  /* USER CODE BEGIN USART2_Init 1 */

  /* USER CODE END USART2_Init 1 */
  huart2.Instance = USART2;
  huart2.Init.BaudRate = 115200;
  huart2.Init.WordLength = UART_WORDLENGTH_8B;
  huart2.Init.StopBits = UART_STOPBITS_1;
  huart2.Init.Parity = UART_PARITY_NONE;
  huart2.Init.Mode = UART_MODE_TX_RX;
  huart2.Init.HwFlowCtl = UART_HWCONTROL_NONE;
  huart2.Init.OverSampling = UART_OVERSAMPLING_16;
  huart2.Init.OneBitSampling = UART_ONE_BIT_SAMPLE_DISABLE;
  huart2.Init.ClockPrescaler = UART_PRESCALER_DIV1;
  huart2.AdvancedInit.AdvFeatureInit = UART_ADVFEATURE_NO_INIT;
  if (HAL_UART_Init(&huart2) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_SetTxFifoThreshold(&huart2, UART_TXFIFO_THRESHOLD_1_4) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_SetRxFifoThreshold(&huart2, UART_RXFIFO_THRESHOLD_1_4) != HAL_OK)
  {
    Error_Handler();
  }
  if (HAL_UARTEx_DisableFifoMode(&huart2) != HAL_OK)
  {
    Error_Handler();
  }
  /* USER CODE BEGIN USART2_Init 2 */

  /* USER CODE END USART2_Init 2 */

}

/**
  * @brief GPIO Initialization Function
  * @param None
  * @retval None
  */
static void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};
  /* USER CODE BEGIN MX_GPIO_Init_1 */

  /* USER CODE END MX_GPIO_Init_1 */

  /* GPIO Ports Clock Enable */
  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOF_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();

  /*Configure GPIO pins : PA1 PA6 */
  GPIO_InitStruct.Pin = GPIO_PIN_1|GPIO_PIN_6;
  GPIO_InitStruct.Mode = GPIO_MODE_ANALOG;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  /* USER CODE BEGIN MX_GPIO_Init_2 */

  /* USER CODE END MX_GPIO_Init_2 */
}

/* USER CODE BEGIN 4 */

/* Callback вызывается по завершении каждого инжектированного преобразования ADC1.
 * LED_GREEN переключается, чтобы на осциллографе видеть момент выборки АЦП.
 * ADC1 в dual mode является ведущим, поэтому его IRQ совпадает с захватом ADC2.
 */
void HAL_ADCEx_InjectedConvCpltCallback(ADC_HandleTypeDef *hadc)
{
    if (hadc->Instance == ADC1)
    {
        BSP_LED_Toggle(LED_GREEN);
    }
}

/* USER CODE END 4 */

/**
  * @brief BSP Push Button callback
  * @param Button Specifies the pressed button
  * @retval None
  */
void BSP_PB_Callback(Button_TypeDef Button)
{
  if (Button == BUTTON_USER)
  {
    BspButtonState = BUTTON_PRESSED;
  }
}

/**
  * @brief  This function is executed in case of error occurrence.
  * @retval None
  */
void Error_Handler(void)
{
  /* USER CODE BEGIN Error_Handler_Debug */
  /* User can add his own implementation to report the HAL error return state */
  __disable_irq();
  while (1)
  {
  }
  /* USER CODE END Error_Handler_Debug */
}
#ifdef USE_FULL_ASSERT
/**
  * @brief  Reports the name of the source file and the source line number
  *         where the assert_param error has occurred.
  * @param  file: pointer to the source file name
  * @param  line: assert_param error line source number
  * @retval None
  */
void assert_failed(uint8_t *file, uint32_t line)
{
  /* USER CODE BEGIN 6 */
  /* User can add his own implementation to report the file name and line number,
     ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
  /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */
