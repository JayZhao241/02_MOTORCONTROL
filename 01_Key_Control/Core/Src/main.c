/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.c
  * @brief          : Main program body
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "dma.h"
#include "tim.h"
#include "usart.h"
#include "gpio.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "zdt_x42s.h"
#include "motor_app.h"
#include "keycontrol.h"

/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */
#define APP_MOTOR_ADDRESS 1U
#define APP_JOG_PULSES    1U  /* Input pulses, NOT degrees. */
#define APP_JOG_RPM       5U
#define APP_JOG_ACCEL     0U   /* Vendor position example: direct low-speed start. */
#define APP_JOG_MODE      0U   /* Vendor position example: previous-target relative. */
/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */

/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/

/* USER CODE BEGIN PV */
/* Keep existing Watch expressions usable after the protocol refactor. */
static volatile ZDT_VersionState_t s_dbg_state = ZDT_VERSION_IDLE;
static volatile bool s_dbg_version_ok = false;
static volatile ZDT_X42S_Version_t s_dbg_version;
static volatile RS485_Result_t s_dbg_comm_result = RS485_OK;

/* Explicit Watch requests: 1 version, 2 status, 3 options, 4 position,
 * 5 recover, 6 move (arm required), 7 voltage, 8 current, 9 target,
 * 10 stop, 11 enable, 12 disable, 13 health, 14 Emm PID, 15 Emm config,
 * 16 integral limit, 17 trajectory position, 18 position error.
 * Nothing is sent on startup. */
static volatile struct {
    uint32_t request;
    uint8_t address;
    uint32_t arm, direction, pulses, speed_rpm;
    RS485_Result_t result; /* Latest NEW request result only; see app.result for execution. */
    uint8_t last_key;
    RS485_Debug_t rs485;
    ZDT_Debug_t zdt;
    MOTOR_AppDebug_t app;
    uint32_t acceleration, mode, timeout_ms;
} s_debug = {.address = APP_MOTOR_ADDRESS, .pulses = APP_JOG_PULSES,
             .speed_rpm = APP_JOG_RPM, .acceleration = APP_JOG_ACCEL,
             .mode = APP_JOG_MODE, .timeout_ms = 10000U};

/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
/* USER CODE BEGIN PFP */

/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
static void Requests_Proc(void)
{
    uint8_t keys = KEY_TakePress();
    uint32_t request = s_debug.request;
    s_debug.request = 0U;
    if (keys != 0U) { s_debug.last_key = keys; }
    if (keys & KEY_EVENT_K0) {
        MOTOR_AppDebug_t app;
        MOTOR_AppGetDebug(&app);
        s_debug.arm = 0U;
        s_debug.result = MOTOR_AppStop(app.address != 0U ? app.address : APP_MOTOR_ADDRESS);
        return; /* Stop wins over simultaneous keys and a Watch move request. */
    }
    if (request != 0U) {
        switch (request) {
        case 1: s_debug.result = MOTOR_AppQuery(s_debug.address, 0x1FU); break;
        case 2: s_debug.result = MOTOR_AppQuery(s_debug.address, 0x3AU); break;
        case 3: s_debug.result = MOTOR_AppQuery(s_debug.address, 0x1AU); break;
        case 4: s_debug.result = MOTOR_AppQuery(s_debug.address, 0x36U); break;
        case 5: s_debug.result = MOTOR_AppRecover(); break;
        case 6: {
            uint32_t arm = s_debug.arm;
            s_debug.arm = 0U;
            const ZDT_EmmMove_t move = {s_debug.address, s_debug.direction,
                s_debug.speed_rpm, s_debug.acceleration, s_debug.pulses,
                s_debug.mode, s_debug.timeout_ms};
            s_debug.result = arm == 0x454D4D31U ? MOTOR_AppMove(&move) : RS485_NOT_READY;
            break;
        }
        case 7: s_debug.result = MOTOR_AppQuery(s_debug.address, 0x24U); break;
        case 8: s_debug.result = MOTOR_AppQuery(s_debug.address, 0x27U); break;
        case 9: s_debug.result = MOTOR_AppQuery(s_debug.address, 0x33U); break;
        case 10: s_debug.result = MOTOR_AppStop(s_debug.address); break;
        case 11: s_debug.result = MOTOR_AppEnable(s_debug.address, true); break;
        case 12: s_debug.result = MOTOR_AppEnable(s_debug.address, false); break;
        case 13: s_debug.result = MOTOR_AppQuery(s_debug.address, 0x3BU); break;
        case 14: s_debug.result = MOTOR_AppQuery(s_debug.address, 0x21U); break;
        case 15: s_debug.result = MOTOR_AppQuery(s_debug.address, 0x42U); break;
        case 16: s_debug.result = MOTOR_AppQuery(s_debug.address, 0x23U); break;
        case 17: s_debug.result = MOTOR_AppQuery(s_debug.address, 0x34U); break;
        case 18: s_debug.result = MOTOR_AppQuery(s_debug.address, 0x37U); break;
        default: s_debug.result = RS485_INVALID_PARAM; break;
        }
        return;
    }
    uint8_t motion_keys = keys & (KEY_EVENT_K1 | KEY_EVENT_K2);
    if (motion_keys == KEY_EVENT_K1 || motion_keys == KEY_EVENT_K2) {
        const ZDT_EmmMove_t move = {APP_MOTOR_ADDRESS,
            motion_keys == KEY_EVENT_K1 ? 0U : 1U, APP_JOG_RPM,
            APP_JOG_ACCEL, APP_JOG_PULSES, APP_JOG_MODE, 10000U};
        s_debug.result = MOTOR_AppMove(&move);
    }
}

static void Debug_Proc(void)
{
    static uint32_t last_tick;
    uint32_t now = HAL_GetTick();
    if ((uint32_t)(now - last_tick) < 50U) { return; }
    last_tick = now;
    RS485_Debug_t driver;
    ZDT_Debug_t protocol;
    MOTOR_AppDebug_t app;
    if (RS485_GetDebug(&driver)) { s_debug.rs485 = driver; }
    if (MOTOR_AppGetDebug(&app)) { s_debug.app = app; }
    if (ZDT_X42S_GetDebug(&protocol)) {
        s_debug.zdt = protocol;
        s_dbg_state = protocol.state;
        s_dbg_comm_result = protocol.comm_result;
        s_dbg_version_ok = protocol.version_valid;
        s_dbg_version.fw_version = protocol.version.fw_version;
        s_dbg_version.hw_series = protocol.version.hw_series;
        s_dbg_version.hw_type = protocol.version.hw_type;
        s_dbg_version.hw_version = protocol.version.hw_version;
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
  MX_DMA_Init();
  MX_TIM6_Init();
  MX_USART2_UART_Init();
  /* USER CODE BEGIN 2 */

  HAL_TIM_Base_Start_IT(&htim6); // 启动定时器中断
  if (RS485_Init() != RS485_OK)
  {
      Error_Handler();
  }

  MOTOR_AppInit(); /* Nonblocking 500ms readiness wait; no automatic movement. */

  /* USER CODE END 2 */

  /* Infinite loop */
  /* USER CODE BEGIN WHILE */
  while (1)
  {
    /* USER CODE END WHILE */

    /* USER CODE BEGIN 3 */
    RS485_Proc();
    ZDT_X42S_Proc();
    Requests_Proc(); /* Keys and Watch share a single application owner. */
    MOTOR_AppProc();
    Debug_Proc();
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
  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

  /** Initializes the RCC Oscillators according to the specified parameters
  * in the RCC_OscInitTypeDef structure.
  */
  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI;
  RCC_OscInitStruct.HSIState = RCC_HSI_ON;
  RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
  RCC_OscInitStruct.PLL.PLLState = RCC_PLL_NONE;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  /** Initializes the CPU, AHB and APB buses clocks
  */
  RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                              |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_HSI;
  RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV1;
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV1;

  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_0) != HAL_OK)
  {
    Error_Handler();
  }
}

/* USER CODE BEGIN 4 */

void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
  if (htim->Instance==TIM6) {
    KEY_Time();
  }
}

/* USER CODE END 4 */

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
