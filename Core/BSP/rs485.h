#ifndef BSP_RS485_H
#define BSP_RS485_H

#include "main.h"
#include <stdint.h>
#include <stdbool.h>

/**
 * @brief RX485驱动操作结果
 * 
 */
typedef enum{
    RS485_OK = 0,
    RS485_BUSY,
    RS485_NOT_READY,
    RS485_INVALID_PARAM,
    RS485_TIMEOUT,
    RS485_RX_OVERFLOW,
    RS485_HW_ERROR
}RS485_Result_t;

/* 初始化RS485驱动 */
RS485_Result_t RS485_Init(void);

/* 异步发送数据 */
RS485_Result_t RS485_SendAsync(const uint8_t *data,uint16_t length);

/* 非阻塞读取接收队列中的数据 */
RS485_Result_t RS485_Read(
    uint8_t *buffer,
    uint16_t capacity,
    uint16_t *received
);
/* 查询发送是否仍在进行 */
bool RS485_IsTxBusy(void);

/* 驱动状态机处理 */
void RS485_Proc(void);

#endif /* BSP_RS485_H */
