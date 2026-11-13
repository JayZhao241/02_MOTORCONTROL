#include "rs485.h"
#include "main.h"
#include "usart.h"
#include <stddef.h>
#include <string.h>

#define RS485_DMA_RX_SIZE   128U
#define RS485_RX_QUEUE_SIZE 256U
#define RS485_TX_BUF_SIZE   128U
#define RS485_TX_TIMEOUT_MS 30U

static volatile uint8_t s_dma_rx_buffer[RS485_DMA_RX_SIZE];
static uint8_t s_rx_queue[RS485_RX_QUEUE_SIZE];
static uint8_t s_tx_buffer[RS485_TX_BUF_SIZE];
static uint16_t s_dma_last_pos;
static volatile uint16_t s_rx_head, s_rx_tail;
static volatile bool s_rx_overflow, s_abort_pending;
static volatile uint32_t s_tx_start_tick;
static volatile RS485_Debug_t s_debug;

static uint32_t Lock(void)
{
    uint32_t mask = __get_PRIMASK();
    __disable_irq();
    return mask;
}

static void Direction(bool tx)
{
    HAL_GPIO_WritePin(RS485_EN_GPIO_Port, RS485_EN_Pin,
                      tx ? GPIO_PIN_SET : GPIO_PIN_RESET);
}

static RS485_Result_t Record(RS485_Result_t result)
{
    if (result != RS485_OK) { s_debug.last_error = result; }
    return result;
}

static RS485_Result_t CheckState(void)
{
    if (s_debug.state == RS485_STATE_UNINIT) { return RS485_NOT_READY; }
    if (s_debug.state == RS485_STATE_FAULT) { return RS485_HW_ERROR; }
    return RS485_OK;
}

static RS485_Result_t FromHal(HAL_StatusTypeDef status)
{
    switch (status) {
    case HAL_OK: return RS485_OK;
    case HAL_BUSY: return RS485_BUSY;
    case HAL_TIMEOUT: return RS485_TIMEOUT;
    default: return RS485_HW_ERROR;
    }
}

RS485_Result_t RS485_Init(void)
{
    uint32_t mask = Lock();
    RS485_Result_t result;
    if (s_debug.state != RS485_STATE_UNINIT) {
        result = s_debug.state == RS485_STATE_READY ? RS485_OK :
                 s_debug.state == RS485_STATE_TX_ACTIVE ? RS485_BUSY : RS485_HW_ERROR;
    } else {
        s_dma_last_pos = 0U; /* Initial DMA start only. Never reset on queue clear. */
        s_rx_head = s_rx_tail = 0U;
        s_rx_overflow = false;
        Direction(false);
        s_debug.state = RS485_STATE_READY;
        HAL_StatusTypeDef status = HAL_UARTEx_ReceiveToIdle_DMA(
            &huart2, (uint8_t *)s_dma_rx_buffer, RS485_DMA_RX_SIZE);
        s_debug.last_hal_status = status;
        result = FromHal(status);
        if (result != RS485_OK) {
            s_debug.last_hal_error = huart2.ErrorCode;
            s_debug.state = RS485_STATE_FAULT;
        }
    }
    Record(result);
    __set_PRIMASK(mask);
    return result;
}

/* RX HT, TC and IDLE must remain enabled. RX DMA and USART2 IRQ have the same
 * preemption priority (6), so these callbacks cannot nest each other.
 * Read the live DMA cursor instead of a possibly stale HT/IDLE Size: an IDLE
 * IRQ serviced before a pending HT IRQ must not replay old bytes as a wrap.
 * CPU must service RX in < half-buffer time (~5.56 ms @ 115200 8N1).
 * No cursor algorithm can recover bytes overwritten by an entire unseen lap. */
void HAL_UARTEx_RxEventCallback(UART_HandleTypeDef *huart, uint16_t Size)
{
    if (huart != &huart2 || Size == 0U || Size > RS485_DMA_RX_SIZE ||
        CheckState() != RS485_OK || huart->hdmarx == NULL) { return; }
    uint32_t remaining = __HAL_DMA_GET_COUNTER(huart->hdmarx);
    if (remaining > RS485_DMA_RX_SIZE) { return; }
    uint16_t pos = (uint16_t)((RS485_DMA_RX_SIZE - remaining) % RS485_DMA_RX_SIZE);
    while (s_dma_last_pos != pos) {
        uint8_t byte = s_dma_rx_buffer[s_dma_last_pos];
        s_dma_last_pos = (uint16_t)((s_dma_last_pos + 1U) % RS485_DMA_RX_SIZE);
        uint16_t next = (uint16_t)((s_rx_head + 1U) % RS485_RX_QUEUE_SIZE);
        if (next == s_rx_tail) {
            if (!s_rx_overflow) { s_debug.overflow_count++; }
            s_rx_overflow = true;
            s_debug.last_error = RS485_RX_OVERFLOW;
        } else {
            s_rx_queue[s_rx_head] = byte;
            __DMB();
            s_rx_head = next;
        }
        s_debug.rx_bytes++;
    }
}

RS485_Result_t RS485_Read(uint8_t *buffer, uint16_t capacity, uint16_t *received)
{
    if (received != NULL) { *received = 0U; }
    if (buffer == NULL || received == NULL || capacity == 0U) {
        return Record(RS485_INVALID_PARAM);
    }
    RS485_Result_t result = CheckState();
    if (result != RS485_OK) { return Record(result); }
    /* Single main-loop consumer; producer publishes bytes before head. */
    uint32_t mask = Lock();
    if (s_rx_overflow) {
        s_rx_tail = s_rx_head;
        s_rx_overflow = false;
        result = RS485_RX_OVERFLOW;
    }
    __set_PRIMASK(mask);
    if (result != RS485_OK) { return Record(result); }
    while (*received < capacity && s_rx_tail != s_rx_head) {
        buffer[*received] = s_rx_queue[s_rx_tail];
        __DMB();
        s_rx_tail = (uint16_t)((s_rx_tail + 1U) % RS485_RX_QUEUE_SIZE);
        (*received)++;
    }
    mask = Lock();
    if (s_rx_overflow || CheckState() != RS485_OK) {
        result = s_rx_overflow ? RS485_RX_OVERFLOW : RS485_HW_ERROR;
        s_rx_tail = s_rx_head;
        s_rx_overflow = false;
        *received = 0U; /* Caller must ignore the partial copy. */
    }
    Record(result);
    __set_PRIMASK(mask);
    return result;
}

RS485_Result_t RS485_ClearRx(void)
{
    uint32_t mask = Lock();
    RS485_Result_t result = CheckState();
    if (result == RS485_OK && s_debug.state == RS485_STATE_TX_ACTIVE) {
        result = RS485_BUSY;
    }
    if (result == RS485_OK) {
        s_rx_tail = s_rx_head;
        s_rx_overflow = false;
    }
    Record(result);
    __set_PRIMASK(mask);
    return result;
}

RS485_Result_t RS485_SendAsync(const uint8_t *data, uint16_t length)
{
    uint32_t mask = Lock();
    RS485_Result_t result = CheckState();
    if (data == NULL || length == 0U || length > RS485_TX_BUF_SIZE) {
        result = RS485_INVALID_PARAM;
    } else if (result == RS485_OK && s_debug.state == RS485_STATE_TX_ACTIVE) {
        result = RS485_BUSY;
    }
    if (result == RS485_OK) {
        memcpy(s_tx_buffer, data, length);
        s_tx_start_tick = HAL_GetTick();
        s_debug.state = RS485_STATE_TX_ACTIVE;
        s_debug.tx_completed = false;
        Direction(true);
        HAL_StatusTypeDef status = HAL_UART_Transmit_DMA(&huart2, s_tx_buffer, length);
        s_debug.last_hal_status = status;
        result = FromHal(status);
        s_debug.last_tx_result = result;
        if (result != RS485_OK) {
            Direction(false);
            s_debug.state = status == HAL_BUSY ? RS485_STATE_READY : RS485_STATE_FAULT;
            s_debug.last_hal_error = huart2.ErrorCode;
        }
    }
    s_debug.last_tx_start_result = result;
    Record(result);
    __set_PRIMASK(mask);
    return result;
}

/* With TX DMA Normal mode HAL calls this from UART TC, after the last stop bit,
 * not from DMA transfer completion. A late callback cannot clear a latched fault. */
void HAL_UART_TxCpltCallback(UART_HandleTypeDef *huart)
{
    if (huart != &huart2) { return; }
    Direction(false);
    if (s_debug.state == RS485_STATE_TX_ACTIVE) {
        s_debug.tx_completed = true;
        s_debug.last_tx_result = RS485_OK;
        s_debug.state = RS485_STATE_READY;
    }
}

void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
    if (huart != &huart2) { return; }
    s_debug.last_hal_error = huart->ErrorCode;
    s_debug.last_error = RS485_HW_ERROR;
    if (s_debug.state == RS485_STATE_TX_ACTIVE) {
        s_debug.last_tx_result = RS485_HW_ERROR;
        s_abort_pending = true;
    }
    s_debug.state = RS485_STATE_FAULT;
    Direction(false);
}

void RS485_Proc(void)
{
    uint32_t mask = Lock();
    if (s_debug.state == RS485_STATE_TX_ACTIVE &&
        (uint32_t)(HAL_GetTick() - s_tx_start_tick) >= RS485_TX_TIMEOUT_MS) {
        s_debug.state = RS485_STATE_FAULT;
        s_debug.last_error = RS485_TIMEOUT;
        s_debug.last_tx_result = RS485_TIMEOUT;
        s_abort_pending = true;
        Direction(false);
    }
    bool abort_tx = s_abort_pending;
    s_abort_pending = false;
    __set_PRIMASK(mask);
    if (abort_tx) {
        /* Asynchronous abort: never restart RX DMA or restore READY here. */
        HAL_StatusTypeDef status = HAL_UART_AbortTransmit_IT(&huart2);
        s_debug.last_hal_status = status;
        if (status != HAL_OK) { s_debug.tx_abort_failed = true; }
    }
}

bool RS485_IsTxBusy(void) { return s_debug.state == RS485_STATE_TX_ACTIVE; }

bool RS485_GetDebug(RS485_Debug_t *out)
{
    if (out == NULL) { return false; }
    uint32_t mask = Lock();
    *out = s_debug;
    out->queued_bytes = (uint16_t)((s_rx_head + RS485_RX_QUEUE_SIZE - s_rx_tail)
                                  % RS485_RX_QUEUE_SIZE);
    __set_PRIMASK(mask);
    return true;
}
