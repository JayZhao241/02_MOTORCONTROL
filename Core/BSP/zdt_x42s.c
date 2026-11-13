/**
 * @file zdt_x42s.c
 * @author 赵英杰
 * @brief 负责电机地址、协议封装
 * @version 0.1
 * @date 2026-10-27
 * 
 * @copyright Copyright (c) 2026
 * 
 */

#include "zdt_x42s.h"
#include "rs485.h"
#include <stddef.h>
#include <stdbool.h>
#include <stdint.h>
#include "stm32f4xx_hal.h"
#include <string.h>

/** ============================================================================
 * @brief 静态变量区域
 * 
 * ========================================================================== */
#define ZDT_VERSION_FRAME_LEN   7U
#define ZDT_VERSION_TIMEOUT_MS  100U
#define ZDT_PROC_MAX_BYTES 16U


/** ============================================================================
 * @brief 结构体，存放查询信息
 * 
 * ========================================================================== */
typedef struct
{
    ZDT_VersionState_t state;

    uint8_t motor_addr;

    uint8_t rx_buf[ZDT_VERSION_FRAME_LEN];

    uint16_t rx_len;

    uint32_t start_tick;

    ZDT_X42S_Version_t version;

    RS485_Result_t comm_result;

} ZDT_VersionQueryContext_t;


/** ============================================================================
 * @brief 普通变量区
 * 
 * ========================================================================== */
static ZDT_VersionQueryContext_t s_query = {
    .state = ZDT_VERSION_IDLE
};




/** ============================================================================
 * @brief 解析电机返回的版本数据
 * 
 * @param frame 接收到的字节
 * @param length 字节长度
 * @param expected_addr 
 * @param version 调用者提供的结构体指针，存放信息
 * @return true 解析成功
 * @return false 
 * ========================================================================== */
bool ZDT_X42S_ParseVersion(
    const uint8_t *frame,
    uint16_t length,
    uint8_t expected_addr,
    ZDT_X42S_Version_t *version
)
{
    // 1. 检查frame、version是否为NULL
    if (frame==NULL||version==NULL) {
        return false;
    }

    // 2. 检查length是否等于7
    if (length!=7) {
        return false;
    }

    // 3. 检查帧格式：
    //    frame[0] == expected_addr
    //    frame[1] == 0x1F
    //    frame[6] == 0x6B
    if (frame[0]!=expected_addr||frame[1] != 0x1F||frame[6] != 0x6B) {
        return false;
    }

    // 4. 解析固件版本
    //    高字节frame[2]，低字节frame[3]
    uint16_t fw_version=((uint16_t)frame[2]<<8)|frame[3];

    // 5. 解析硬件系列和类型
    //    frame[4]的高4位、低4位
    uint8_t hw_series=frame[4]>>4;
    uint8_t hw_type=frame[4]&0x0FU;

    // 6. 解析硬件版本
    //    frame[5]
    uint8_t hw_version = frame[5];

    version->fw_version = fw_version;
    version->hw_series = hw_series;
    version->hw_type = hw_type;
    version->hw_version = hw_version;


    // 7. 解析成功，返回true
    return true;
}

/** ============================================================================
 * @brief 发送查询指令
 * 
 * @param motor_addr 
 * @return RS485_Result_t 
 * ========================================================================== */
RS485_Result_t ZDT_X42S_RequestVersion(
    uint8_t motor_addr
)
{
    // 1. 创建一个3字节数组
    // 第一个字节：motor_addr
    // 第二个字节：0x1F
    // 第三个字节：0x6B
    const uint8_t cmd[3]={
        motor_addr,
        0x1FU,
        0x6BU
    };


    // 2. 调用RS485_SendAsync()
    // 并将它的返回结果交给调用者
    return RS485_SendAsync(cmd, sizeof(cmd));

}

/** ============================================================================
 * @brief 检查查询状态，启动发送，记录查询上下文
 * 
 * @param motor_addr 
 * @return RS485_Result_t 
 * ========================================================================== */

RS485_Result_t ZDT_X42S_StartVersionQuery(
    uint8_t motor_addr
)
{
    // 1. 已有查询进行中，不允许启动新查询
    if (s_query.state == ZDT_VERSION_WAIT_RESPONSE)
    {
        return RS485_BUSY;
    }

    // 2. 清空软件接收队列中已经入队的旧字节
    RS485_Result_t clear_result = RS485_ClearRx();

    if (clear_result != RS485_OK)
    {
        s_query.comm_result = clear_result;
        s_query.state = ZDT_VERSION_ERROR;
        return clear_result;
    }

    // 3. 发送版本查询命令
    RS485_Result_t result =
        ZDT_X42S_RequestVersion(motor_addr);

    if (result != RS485_OK)
    {
        s_query.comm_result = result;
        s_query.state = ZDT_VERSION_ERROR;
        return result;
    }

    // 4. 记录本次查询
    s_query.motor_addr = motor_addr;
    s_query.rx_len = 0U;
    s_query.start_tick = HAL_GetTick();
    s_query.comm_result = RS485_OK;

    // 5. 进入等待状态
    s_query.state = ZDT_VERSION_WAIT_RESPONSE;

    return RS485_OK;
}


/** ============================================================================
 * @brief 处理接收和超时
 * 
 * ========================================================================== */

void ZDT_X42S_Proc(void)
{
    // 1. 不在等待状态，直接返回
    if (s_query.state != ZDT_VERSION_WAIT_RESPONSE)
    {
        return;
    }

    // 2. 首先检查本次查询是否已经超时
    if ((uint32_t)(HAL_GetTick() - s_query.start_tick)
        >= ZDT_VERSION_TIMEOUT_MS)
    {
        s_query.comm_result = RS485_TIMEOUT;
        s_query.state = ZDT_VERSION_TIMEOUT;
        return;
    }

    // 3. 每次调用最多处理16个接收字节
    for (uint8_t i = 0U; i < ZDT_PROC_MAX_BYTES; i++)
    {
        uint8_t byte = 0U;
        uint16_t count = 0U;

        RS485_Result_t result = RS485_Read(
            &byte,
            1U,
            &count
        );

        // 4. 底层读取异常
        if (result != RS485_OK)
        {
            s_query.comm_result = result;
            s_query.state = ZDT_VERSION_ERROR;
            return;
        }

        // 5. 暂时没有新数据
        if (count == 0U)
        {
            break;
        }

        // 6. 把新字节追加到窗口中
        s_query.rx_buf[s_query.rx_len] = byte;
        s_query.rx_len++;

        // 7. 尚未收满7字节
        if (s_query.rx_len < ZDT_VERSION_FRAME_LEN)
        {
            continue;
        }

        // 8. 收满7字节，检查是否为正确回复
        bool ok = ZDT_X42S_ParseVersion(
            s_query.rx_buf,
            s_query.rx_len,
            s_query.motor_addr,
            &s_query.version
        );

        if (ok)
        {
            s_query.state = ZDT_VERSION_SUCCESS;
            return;
        }

        // 9. 不是正确的回复，窗口左移一字节
        memmove(
            &s_query.rx_buf[0],
            &s_query.rx_buf[1],
            ZDT_VERSION_FRAME_LEN - 1U
        );

        // 10. 保留后面6个字节，等待下一个字节
        s_query.rx_len = ZDT_VERSION_FRAME_LEN - 1U;
    }

    // 11. 本轮处理结束后，再检查一次超时
    if ((uint32_t)(HAL_GetTick() - s_query.start_tick)
        >= ZDT_VERSION_TIMEOUT_MS)
    {
        s_query.comm_result = RS485_TIMEOUT;
        s_query.state = ZDT_VERSION_TIMEOUT;
    }
}


/** ============================================================================
 * @brief 获取查询状态
 * 
 * @return ZDT_VersionState_t 
 * ========================================================================== */
ZDT_VersionState_t ZDT_X42S_GetVersionState(void)
{
    return s_query.state;
}

RS485_Result_t ZDT_X42S_GetCommResult(void)
{
    return s_query.comm_result;
}


bool ZDT_X42S_GetVersion(
    ZDT_X42S_Version_t *out_version
)
{
    // 1. 检查输出指针
    if (out_version == NULL)
    {
        return false;
    }

    // 2. 只有查询成功才能获取版本
    if (s_query.state != ZDT_VERSION_SUCCESS)
    {
        return false;
    }

    // 3. 将整个结构体复制给调用者
    *out_version = s_query.version;

    // 4. 返回获取成功
    return true;
}

