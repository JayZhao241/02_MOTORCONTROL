/** ============================================================================
 * @file rs485.c
 * @author 赵英杰 (muyang2410@163.com)
 * @brief 
 * @version 0.1
 * @date 2026-10-29
 * 
 * @copyright Copyright (c) 2026
 * 
 * ========================================================================== */

#include "rs485.h"
#include "stm32f407xx.h"
#include "stm32f4xx_hal.h"
#include "stm32f4xx_hal_def.h"
#include "stm32f4xx_hal_gpio.h"
#include "stm32f4xx_hal_uart.h"
#include "usart.h"
#include <cstddef>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

/** ============================================================================
 * @brief 静态变量区域
 * 
 * ========================================================================== */
#define RS485_DMA_RX_SIZE  128U // DMA接收缓存区容量
#define RS485_RX_QUEUE_SIZE 256U // 
#define RS485_TX_BUF_SIZE  128U
#define RS485_TX_TIMEOUT_MS 30U //发送超时时间

/** ============================================================================
 * @brief 变量区域
 * 
 * ========================================================================== */
static uint8_t s_dma_rx_buffer[RS485_DMA_RX_SIZE]; // DMA接收到的数据保存地址
static uint8_t s_rx_queue[RS485_RX_QUEUE_SIZE]; // 保存等待处理的接收数据
static uint8_t s_tx_buffer[RS485_TX_BUF_SIZE]; // 保存需要发送出去的字节

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

static volatile bool s_rx_overflow = false; // 接收队列溢出标志

static volatile uint32_t s_tx_start_tick = 0U; // 记录发送超时的变量

/** ============================================================================
 * @brief 判断接收数据暂存情况
 * 
 * @param data 
 * @return true 
 * @return false 
 * ========================================================================== */
static bool RS485_RxPushByte(uint8_t data)
{
    // 1. 计算下一个写入位置next_head
    uint16_t next_head=(s_rx_head+1U) % RS485_RX_QUEUE_SIZE;

    // 2. 判断队列是否已满
    //    已满则返回false
    if (next_head==s_rx_tail) {
        return false;
    }

    // 3. 将data写入s_rx_queue[s_rx_head]
    s_rx_queue[s_rx_head]=data;

    // 4. 更新s_rx_head
    s_rx_head=next_head;

    // 5. 返回true
    return true;
}


/** ============================================================================
 * @brief 检查接收缓冲区是否溢出
 * 
 * @return true 溢出，丢弃尚未读取的数据
 * @return false 
 * ========================================================================== */
static bool RS485_RxCheckAndClearOverflow(void)
{
    // 保存进入函数时的中断屏蔽状态
    uint32_t primask = __get_PRIMASK();

    // 临时屏蔽可屏蔽中断
    __disable_irq();

    bool overflow = s_rx_overflow;

    if (overflow)
    {
        // 丢弃队列中尚未读取的数据
        s_rx_tail = s_rx_head;

        // 清除本次已经记录的溢出标志
        s_rx_overflow = false;
    }

    // 恢复进入函数前的中断屏蔽状态
    __set_PRIMASK(primask);

    return overflow;
}


/** ============================================================================
 * @brief 驱动初始化函数
 * 
 * @return RS485_Result_t 
 * ========================================================================== */
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
    s_rx_overflow=false;

    // 4. PG8输出低电平，进入RS485接收模式
    HAL_GPIO_WritePin(GPIOG, GPIO_PIN_8, GPIO_PIN_RESET);

    // 5. 调用HAL_UARTEx_ReceiveToIdle_DMA()
    //    将函数返回值保存到hal_status
    hal_status = HAL_UARTEx_ReceiveToIdle_DMA(&huart2, s_dma_rx_buffer, RS485_DMA_RX_SIZE);

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

/** ============================================================================
 * @brief 回调函数、处理dma数据
 * 
 * @param huart 
 * @param Size 
 * ========================================================================== */
void HAL_UARTEx_RxEventCallback(
    UART_HandleTypeDef *huart,
    uint16_t Size
)
{
    if (huart != &huart2 ||
        Size == 0U ||
        Size > RS485_DMA_RX_SIZE)
    {
        return;
    }

    if (Size > s_dma_last_pos)
    {
        // 情况1：处理未回绕的数据
        for (uint16_t i= s_dma_last_pos; i<Size; i++) {
            uint8_t data = s_dma_rx_buffer[i];

            if (RS485_RxPushByte(data) == false)
            {
                s_rx_overflow = true;
            }
        }
    }
    else if (Size < s_dma_last_pos)
    {
        // 情况2：处理缓冲区末尾和开头的数据
        for (uint16_t i = s_dma_last_pos; i<RS485_DMA_RX_SIZE; i++) {
            uint8_t data = s_dma_rx_buffer[i];
            if (RS485_RxPushByte(data) == false)
            {
                s_rx_overflow = true;
            }
        }
        for (uint16_t i = 0; i<Size; i++) {
            uint8_t data = s_dma_rx_buffer[i];
            if (RS485_RxPushByte(data) == false)
            {
                s_rx_overflow = true;
            }
        }
    }
    else
    {
        // 情况3：当前位置没有变化
        return;
    }

    // 数据成功处理后，更新s_dma_last_pos
    s_dma_last_pos = Size;
}

/** ============================================================================
 * @brief 主程序从接收队列取出电机回复数据
 * 
 * @param buffer 存放读出数据的数组
 * @param capacity 可以存放数据的大小
 * @param received 实际上读取了多少数据
 * @return RS485_Result_t 
 * ========================================================================== */
RS485_Result_t RS485_Read(
    uint8_t *buffer,
    uint16_t capacity,
    uint16_t *received
)
{
    if (buffer==NULL || received == NULL ||capacity==0U) {
        return RS485_INVALID_PARAM;
    }

    *received = 0U;

    switch (s_state) {
        case RS485_STATE_READY:
        case RS485_STATE_TX_ACTIVE:
            break;

        case RS485_STATE_UNINIT:
            return RS485_NOT_READY;

        case RS485_STATE_FAULT:
        default:
            return RS485_HW_ERROR;
    }

    // 检查数据是否溢出
    if (RS485_RxCheckAndClearOverflow())
    {
        return RS485_RX_OVERFLOW;
    }

    while ((*received < capacity)&&(s_rx_tail!=s_rx_head)) {
        buffer[*received]=s_rx_queue[s_rx_tail];
        s_rx_tail = (s_rx_tail + 1U) % RS485_RX_QUEUE_SIZE;
        (*received)++;
    }
    if (RS485_RxCheckAndClearOverflow())
    {
        *received = 0U;
        return RS485_RX_OVERFLOW;
    }

    return RS485_OK;
}

/** ============================================================================
 * @brief 向电机发送指令
 * 
 * @param data 
 * @param length 
 * @return RS485_Result_t 
 * ========================================================================== */
RS485_Result_t RS485_SendAsync(
    const uint8_t *data,
    uint16_t length
)
{
    if (data==NULL||length==0U||length>RS485_TX_BUF_SIZE) {
        return RS485_INVALID_PARAM;
    }

    switch (s_state) {
        case RS485_STATE_READY:
            break;
        case RS485_STATE_TX_ACTIVE:
            return RS485_BUSY;

        case RS485_STATE_UNINIT:
            return RS485_NOT_READY;

        case RS485_STATE_FAULT:
        default:
            return RS485_HW_ERROR;
    }
    memcpy(s_tx_buffer, data, length);

    s_tx_start_tick=HAL_GetTick();
    s_state=RS485_STATE_TX_ACTIVE;
    HAL_GPIO_WritePin(GPIOG, GPIO_PIN_8, GPIO_PIN_SET);

    
    HAL_StatusTypeDef hal_status = HAL_UART_Transmit_DMA(&huart2, s_tx_buffer, length);
    if (hal_status!=HAL_OK) {
        HAL_GPIO_WritePin(GPIOG, GPIO_PIN_8, GPIO_PIN_RESET);

        if (hal_status==HAL_BUSY) {
            return RS485_BUSY;
        }

        s_state = RS485_STATE_FAULT;
        if (hal_status==HAL_TIMEOUT) {
            return RS485_TIMEOUT;
        }

        return RS485_HW_ERROR;
    }

    return RS485_OK;
}

/** ============================================================================
 * @brief 发送回调函数
 * 
 * @param huart 
 * ========================================================================== */
void HAL_UART_TxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart!=&huart2) {
        return;
    }

    HAL_GPIO_WritePin(GPIOG, GPIO_PIN_8, GPIO_PIN_RESET);
    if (s_state==RS485_STATE_TX_ACTIVE) {
        s_state=RS485_STATE_READY;
    }
}

/** ============================================================================
 * @brief UART错误回调函数
 * 
 * @param huart 
 * ========================================================================== */
void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
    if (huart!=&huart2) {
        return;
    }

    HAL_GPIO_WritePin(GPIOG, GPIO_PIN_8, GPIO_PIN_RESET);
    s_state=RS485_STATE_FAULT;
}

void RS485_Proc(){
    if (s_state!=RS485_STATE_TX_ACTIVE) {
        return;
    }

    uint32_t elapsed = HAL_GetTick() - s_tx_start_tick;

    if (elapsed>=RS485_TX_TIMEOUT_MS) {
        s_state=RS485_STATE_FAULT;
    }
}
