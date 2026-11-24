#ifndef BSP_RS485_H
#define BSP_RS485_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    RS485_OK = 0, RS485_BUSY, RS485_NOT_READY, RS485_INVALID_PARAM,
    RS485_TIMEOUT, RS485_RX_OVERFLOW, RS485_HW_ERROR
} RS485_Result_t;

typedef enum {
    RS485_STATE_UNINIT = 0, RS485_STATE_READY,
    RS485_STATE_TX_ACTIVE, RS485_STATE_FAULT
} RS485_State_t;

/* Snapshot API: call from the main loop, never from an interrupt. */
typedef struct {
    RS485_State_t state;
    uint32_t last_hal_error;       /* UART ErrorCode, retained after failure. */
    uint32_t last_hal_status;      /* HAL_StatusTypeDef of the last HAL operation. */
    RS485_Result_t last_error;     /* Sticky last non-OK operation result. */
    RS485_Result_t last_tx_start_result;
    RS485_Result_t last_tx_result; /* OK + tx_completed means UART TC occurred. */
    bool tx_completed;
    bool tx_abort_failed;
    uint32_t rx_bytes;
    uint32_t overflow_count;
    uint16_t queued_bytes;
} RS485_Debug_t;

/* All public operations belong to one main-loop owner. USART2 callbacks only
 * move bytes/update driver state; only the ZDT protocol module consumes RX. */
RS485_Result_t RS485_Init(void);
RS485_Result_t RS485_SendAsync(const uint8_t *data, uint16_t length);
RS485_Result_t RS485_Read(uint8_t *buffer, uint16_t capacity, uint16_t *received);
bool RS485_IsTxBusy(void);
void RS485_Proc(void);
/* Drops ONLY bytes already published to the software queue at the critical
 * section. Does not discard unreported DMA bytes or future/late replies.
 * Does not restart DMA or change its read cursor. Refuses during TX. */
RS485_Result_t RS485_ClearRx(void);
bool RS485_GetDebug(RS485_Debug_t *out);

#endif
