/**
 * @file rs485.c
 * @author 赵英杰 (muyang2410@163.com)
 * @brief 
 * @version 0.1
 * @date 2026-10-29
 * 
 * @copyright Copyright (c) 2026
 * 
 */

#include "rs485.h"
#include "stm32f407xx.h"
#include "stm32f4xx_hal.h"
#include "stm32f4xx_hal_def.h"
#include "stm32f4xx_hal_gpio.h"
#include "stm32f4xx_hal_uart.h"
#include "usart.h"
#include <stdint.h>

#define RS485_DMA_RX_SIZE  128U // DMA接收缓存区容量
#define RS485_RX_QUEUE_SIZE 256U
#define RS485_TX_BUF_SIZE  128U

static uint8_t s_dma_rx_buffer[RS485_DMA_RX_SIZE]; // DMA接收到的数据保存地址
static uint8_t s_rx_queue[RS485_RX_QUEUE_SIZE];
static uint8_t s_tx_buffer[RS485_TX_BUF_SIZE];

typedef enum{
    RS485_STATE_UNINIT = 0,
    RS485_STATE_READY,
    RS485_STATE_TX_ACTIVE,
    RS485_STATE_FAULT
} RS485_State_t;

static RS485_State_t s_state = RS485_STATE_UNINIT; // 定义驱动状态

static uint16_t s_dma_last_pos = 0U; // 上一次缓冲区读取到的位置
static volatile uint16_t s_rx_head = 0U; // 软件接收队列中，下一个写入位置。
static volatile uint16_t s_rx_tail = 0U; // 软件接收队列中，下一个读取位置。

RS485_Result_t RS485_Init(void)
{
    HAL_StatusTypeDef hal_status;

    // 1. 检查s_state，避免重复初始化
    switch (s_state) {
        case RS485_STATE_UNINIT:
            break;
        case RS485_STATE_READY:
            return RS485_OK;
        case RS485_STATE_TX_ACTIVE:
            return RS485_BUSY;
        case RS485_STATE_FAULT:
        default:
            return RS485_HW_ERROR;
    }

    // 2. 将s_dma_last_pos、s_rx_head、s_rx_tail复位
    s_dma_last_pos = 0U;
    s_rx_head = 0U;
    s_rx_tail = 0U;

    // 3. 如果后续有其他接收错误标志，一并复位

    // 4. PG8输出低电平，进入RS485接收模式
    HAL_GPIO_WritePin(GPIOG, GPIO_PIN_8, GPIO_PIN_RESET);

    // 5. 调用HAL_UARTEx_ReceiveToIdle_DMA()
    //    将函数返回值保存到hal_status
    uint8_t hal_status = HAL_UARTEx_ReceiveToIdle_DMA(&huart2, s_dma_rx_buffer, RS485_DMA_RX_SIZE);

    // 6. 根据hal_status判断初始化是否成功
    switch (hal_status) {
        case HAL_OK:
            s_state = RS485_STATE_READY;
            return RS485_OK;
        case HAL_TIMEOUT:
            s_state = RS485_STATE_FAULT;
            return RS485_TIMEOUT;
        case HAL_BUSY:
            s_state = RS485_STATE_FAULT;
            return RS485_BUSY;
        case HAL_ERROR:
        default:
            s_state = RS485_STATE_FAULT;
            return RS485_HW_ERROR;
    }

    // 7. 成功后设置s_state为READY，返回RS485_OK
    //    失败则设置FAULT，并返回对应错误
}
