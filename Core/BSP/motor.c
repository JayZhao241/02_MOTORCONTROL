#include "motor.h"
#include "main.h"
#include "stm32f4xx_hal.h"
#include "stm32f4xx_hal_gpio.h"
#include <stdint.h>

// 控制电机发送一个脉冲
static void MOTOR_SendOnePulse()
{
    HAL_GPIO_WritePin(MOTOR_STEP_GPIO_Port, MOTOR_STEP_Pin, GPIO_PIN_SET);
    HAL_Delay(2);
    HAL_GPIO_WritePin(MOTOR_STEP_GPIO_Port, MOTOR_STEP_Pin, GPIO_PIN_RESET);
    HAL_Delay(2);
}

// 控制电机朝某一方向旋转一个角度
/*
angle: 旋转角度，0-360，可以是小数
dir: 方向，0为顺时针，1为逆时针
*/
void MOTOR_Control(float angle, uint8_t dir)
{
    // 发送的脉冲数只能为整数
    float pulse_origin=(angle*3200.0f)/360.0f;
    uint32_t pulse_count = (uint32_t)(pulse_origin + 0.5f);

    // 方向设置
    if (dir==0) {
        HAL_GPIO_WritePin(MOTOR_DIR_GPIO_Port, MOTOR_DIR_Pin, GPIO_PIN_RESET);
    }
    else if(dir==1) {
        HAL_GPIO_WritePin(MOTOR_DIR_GPIO_Port, MOTOR_DIR_Pin, GPIO_PIN_SET);
    }
    else {
        return;
    }
    HAL_Delay(1);

    for (uint32_t i=0; i<pulse_count; i++) {
        MOTOR_SendOnePulse();
    }
}
