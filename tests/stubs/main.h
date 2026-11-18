#include "stm32f4xx_hal.h"
#define RS485_EN_GPIO_Port 7
#define RS485_EN_Pin 8
#define K0_GPIO_Port 4
#define K0_Pin (1U << 4)
#define K1_GPIO_Port 4
#define K1_Pin (1U << 3)
#define K2_GPIO_Port 4
#define K2_Pin (1U << 2)
#define MOTOR_STEP_GPIO_Port 0
#define MOTOR_STEP_Pin (1U << 6)
#define MOTOR_DIR_GPIO_Port 1
#define MOTOR_DIR_Pin (1U << 6)
