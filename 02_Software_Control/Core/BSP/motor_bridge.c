#include "motor_bridge.h"
#include "emm_v5.h"
#include "stm32f4xx_hal.h"
#include <string.h>
volatile MOTOR_Bridge_t s_bridge;
static uint32_t started, received, heartbeat;
static bool waiting;
static uint8_t stopping;
static uint32_t stop_started;
static void Diagnostics(void)
{
    RS485_Debug_t debug;
    if (RS485_GetDebug(&debug)) {
        s_bridge.driver_state=debug.state; s_bridge.hal_error=debug.last_hal_error;
        s_bridge.hal_status=debug.last_hal_status; s_bridge.driver_error=debug.last_error;
        s_bridge.tx_result=debug.last_tx_result;
    }
}
static void StopAll(uint32_t now)
{
    stopping=1U; stop_started=now; waiting=false;
    s_bridge.stop_requested_mask=s_bridge.stop_failed_mask=0U;
}
static bool Valid(const uint8_t *p, uint32_t n)
{
    if (n < 3U || n > 20U || p[n-1U] != 0x6BU) { return false; }
    if (n == 4U && p[0] == 0U && p[1] == 0xFFU && p[2] == 0x66U) { return true; }
    if (p[0] != 1U && p[0] != 2U) { return false; }
    switch (p[1]) {
    case 0x42: return n == 4U && p[2] == 0x6CU;
    case 0xFD: return n == 13U && p[2] <= 1U &&
        (((uint32_t)p[3]<<8)|p[4]) > 0U && (((uint32_t)p[3]<<8)|p[4]) <= 3000U && p[10] <= 2U && p[11] <= 1U;
    case 0xF3: return n == 6U && p[2] == 0xABU && p[3] <= 1U && p[4] == 0U;
    case 0xFE: return n == 5U && p[2] == 0x98U && p[3] == 0U;
    case 0x93: return n == 5U && p[2] == 0x88U && p[3] == 1U;
    case 0x0A: return n == 4U && p[2] == 0x6DU;
    case 0x4C: return n == 20U && p[2] == 0xAEU && p[3] == 1U;
    default: return n == 3U && EMM_V5_ReplyLength(p[1]) != 0U && !EMM_V5_IsAckFunction(p[1]);
    }
}
bool MOTOR_BridgeProc(bool legacy_busy, bool stop_key)
{
    uint32_t now = HAL_GetTick(), request = s_bridge.request;
    Diagnostics();
    if (stop_key && s_bridge.active && !stopping) { StopAll(now); }
    if (!stopping && s_bridge.active && now-heartbeat >= 10000U) { StopAll(now); }
    if (stopping) {
        /* One attempt per address. A latched UART fault must not trap the
         * owner forever or silently report that either motor has stopped. */
        if (RS485_IsTxBusy() && now-stop_started < 500U) {
            s_bridge.request=0U;
            return true;
        }
        if (stopping <= 2U) {
            uint8_t frame[5];
            EMM_V5_BuildStop(stopping,frame,sizeof(frame));
            RS485_Result_t r=RS485_SendAsync(frame,sizeof(frame));
            uint32_t bit=1UL << (stopping-1U);
            if (r == RS485_OK) { s_bridge.stop_requested_mask |= bit; }
            else { s_bridge.stop_failed_mask |= bit; }
            stopping++;
        } else {
            Diagnostics();
            if (s_bridge.driver_state == RS485_STATE_FAULT) {
                s_bridge.stop_failed_mask |= s_bridge.stop_requested_mask;
            }
            stopping=0U; waiting=false; s_bridge.active=0U;
            s_bridge.result=s_bridge.stop_failed_mask ? RS485_HW_ERROR : RS485_OK;
        }
        s_bridge.request=0U;
        return true;
    }
    if (request) {
        s_bridge.request = 0U;
        if (s_bridge.arm != 0x4455414CU) { s_bridge.result = RS485_NOT_READY; return s_bridge.active != 0U; }
        s_bridge.arm = 0U;
        if (request == 1U) {
            if (s_bridge.driver_state == RS485_STATE_FAULT) { s_bridge.result=RS485_HW_ERROR; }
            else if (legacy_busy || waiting || RS485_IsTxBusy()) { s_bridge.result = RS485_BUSY; }
            else { s_bridge.active = 1U; heartbeat = now; s_bridge.result = RS485_OK; }
        } else if (request == 3U && s_bridge.active) {
            StopAll(now); /* Always attempt both, including after a UART fault. */
        } else if (request == 2U && s_bridge.active && !waiting) {
            uint8_t tx[20]; uint32_t n = s_bridge.tx_len;
            if (n <= sizeof(tx)) { for (uint32_t i=0; i<n; ++i) { tx[i]=s_bridge.tx[i]; } }
            if (n > sizeof(tx) || !Valid(tx,n)) { s_bridge.result = RS485_INVALID_PARAM; }
            else {
                RS485_Result_t r = RS485_ClearRx();
                if (r == RS485_OK) { r = RS485_SendAsync(tx,(uint16_t)n); }
                s_bridge.rx_len = 0U; s_bridge.result = r;
                if (r == RS485_OK) { waiting=true; started=received=heartbeat=now; s_bridge.result=RS485_BUSY; }
            }
        } else { s_bridge.result = RS485_NOT_READY; }
    }
    if (waiting) {
        uint8_t rx[32]; uint16_t n=0;
        RS485_Result_t r=RS485_Read(rx,sizeof(rx),&n);
        if (r != RS485_OK) { waiting=false; s_bridge.result=r; }
        else {
            for (uint16_t i=0; i<n; ++i) {
                if (s_bridge.rx_len < sizeof(s_bridge.rx)) { s_bridge.rx[s_bridge.rx_len++]=rx[i]; }
                else { waiting=false; s_bridge.result=RS485_RX_OVERFLOW; break; }
            }
            if (n) { received=now; }
            if (waiting && !RS485_IsTxBusy() && ((s_bridge.rx_len && now-received>=10U) || now-started>=1500U ||
                (s_bridge.tx[0]==0U && now-started>=20U))) { waiting=false; s_bridge.result=RS485_OK; }
        }
    }
    Diagnostics();
    return s_bridge.active != 0U;
}
