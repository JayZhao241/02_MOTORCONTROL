#ifndef ZDT_X42S_H
#define ZDT_X42S_H

#include "rs485.h"
#include <stdbool.h>

typedef struct
{
    uint16_t fw_version;
    uint8_t hw_series;
    uint8_t hw_type;
    uint8_t hw_version;
} ZDT_X42S_Version_t;

typedef enum
{
    ZDT_VERSION_IDLE = 0,
    ZDT_VERSION_WAIT_RESPONSE,
    ZDT_VERSION_SUCCESS,
    ZDT_VERSION_TIMEOUT,
    ZDT_VERSION_ERROR

} ZDT_VersionState_t;

bool ZDT_X42S_ParseVersion(
    const uint8_t *frame,
    uint16_t length,
    uint8_t expected_addr,
    ZDT_X42S_Version_t *version
);

RS485_Result_t ZDT_X42S_RequestVersion(
    uint8_t motor_addr
);

RS485_Result_t ZDT_X42S_StartVersionQuery(
    uint8_t motor_addr
);

void ZDT_X42S_Proc(void);

ZDT_VersionState_t ZDT_X42S_GetVersionState(void);

/* 最近一次查询的通信结果；是否完成仍需检查版本查询状态。 */
RS485_Result_t ZDT_X42S_GetCommResult(void);

bool ZDT_X42S_GetVersion(
    ZDT_X42S_Version_t *out_version
);

#endif /* ZDT_X42S_H */
