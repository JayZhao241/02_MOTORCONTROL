#include "motor_app.h"
#include "stm32f4xx_hal.h"
#include <stddef.h>
#include <string.h>

#define MOTOR_STARTUP_MS 500U /* Supplied vendor main.c waits 500ms. */
#define MOTOR_FRAME_GAP_MS 10U
#define MOTOR_SEND_WAIT_MS 500U
static MOTOR_AppDebug_t s_app;
static uint32_t boot_tick, stage_tick, last_step_tick;

static bool Active(void)
{
    switch (s_app.stage) {
    case MOTOR_APP_BOOT_WAIT: case MOTOR_APP_IDLE: case MOTOR_APP_DONE:
    case MOTOR_APP_STOP_ACCEPTED: case MOTOR_APP_FAILED: return false;
    default: return true;
    }
}
static RS485_Result_t RecordRequest(RS485_Result_t result)
{
    s_app.last_request_result = result;
    if (result == RS485_OK) { s_app.accepted_requests++; }
    else { s_app.rejected_requests++; }
    return result;
}
static RS485_Result_t Available(uint8_t address)
{
    const ZDT_Debug_t *p = ZDT_X42S_PeekDebug();
    if (!address) { return RS485_INVALID_PARAM; }
    if (Active() || p->transaction == ZDT_TXN_WAIT || p->motion == ZDT_MOTION_WAIT_ACK ||
        p->motion == ZDT_MOTION_ACCEPTED || p->motion == ZDT_MOTION_MOVING) { return RS485_BUSY; }
    if (s_app.stage == MOTOR_APP_BOOT_WAIT || p->resync_required ||
        p->motion == ZDT_MOTION_UNKNOWN || p->motion == ZDT_MOTION_TIMEOUT ||
        p->motion == ZDT_MOTION_FAULT || p->motion == ZDT_MOTION_STOP_REQUESTED) {
        return RS485_NOT_READY;
    }
    return RS485_OK;
}
static void Stage(MOTOR_AppStage_t stage)
{
    s_app.stage = stage;
    s_app.waiting_for_reply = false;
    stage_tick = HAL_GetTick();
    s_app.active = Active();
}
static void Start(uint8_t address, MOTOR_AppStage_t stage)
{
    s_app.address = address;
    s_app.error = MOTOR_APP_NO_ERROR;
    s_app.result = RS485_OK;
    s_app.failed_stage = MOTOR_APP_IDLE;
    Stage(stage);
}
static void Fail(RS485_Result_t result, MOTOR_AppError_t error)
{
    s_app.failed_stage = s_app.stage;
    s_app.result = result;
    s_app.error = error;
    Stage(MOTOR_APP_FAILED);
}
void MOTOR_AppInit(void)
{
    memset(&s_app, 0, sizeof(s_app));
    s_app.stage = MOTOR_APP_BOOT_WAIT;
    boot_tick = stage_tick = last_step_tick = HAL_GetTick();
}
RS485_Result_t MOTOR_AppMove(const ZDT_EmmMove_t *move)
{
    uint8_t frame[EMM_V5_MAX_FRAME];
    if (!EMM_V5_BuildPosition(move, frame, sizeof(frame))) { return RecordRequest(RS485_INVALID_PARAM); }
    RS485_Result_t result = Available(move->address);
    if (result == RS485_OK) {
        s_app.move = *move;
        Start(move->address, MOTOR_APP_OPTIONS);
    }
    return RecordRequest(result);
}
RS485_Result_t MOTOR_AppQuery(uint8_t address, uint8_t function)
{
    uint8_t frame[4];
    if (!EMM_V5_BuildRead(address, function, frame, sizeof(frame))) { return RecordRequest(RS485_INVALID_PARAM); }
    RS485_Result_t result = Available(address);
    if (result == RS485_OK) { s_app.query_function = function; Start(address, MOTOR_APP_QUERY); }
    return RecordRequest(result);
}
RS485_Result_t MOTOR_AppEnable(uint8_t address, bool enable)
{
    RS485_Result_t result = Available(address);
    if (result == RS485_OK) { Start(address, enable ? MOTOR_APP_ENABLE_ONLY : MOTOR_APP_DISABLE_ONLY); }
    return RecordRequest(result);
}
RS485_Result_t MOTOR_AppStop(uint8_t address)
{
    if (!address) { return RecordRequest(RS485_INVALID_PARAM); }
    if (s_app.stage == MOTOR_APP_STOPPING) { return RecordRequest(RS485_BUSY); }
    if (s_app.stage == MOTOR_APP_BOOT_WAIT) { return RecordRequest(RS485_NOT_READY); }
    /* A new stop replaces preparation/monitoring. The protocol layer waits for
     * UART TX completion and ignores the cancelled transaction's late frames. */
    if (Active()) {
        if (s_app.address != address) { return RecordRequest(RS485_INVALID_PARAM); }
        s_app.cancelled_requests++;
    }
    Start(address, MOTOR_APP_STOPPING);
    return RecordRequest(RS485_OK);
}
RS485_Result_t MOTOR_AppRecover(void)
{
    if (s_app.stage == MOTOR_APP_BOOT_WAIT) { return RecordRequest(RS485_NOT_READY); }
    if (Active()) { return RecordRequest(RS485_BUSY); }
    RS485_Result_t result = ZDT_X42S_ResumeAfterTimeout();
    if (result == RS485_OK) { Start(s_app.address, MOTOR_APP_IDLE); }
    return RecordRequest(result);
}
static RS485_Result_t SendStage(void)
{
    switch (s_app.stage) {
    case MOTOR_APP_OPTIONS: return ZDT_X42S_StartOptionsQuery(s_app.address);
    case MOTOR_APP_ENABLE: case MOTOR_APP_ENABLE_ONLY: return ZDT_X42S_StartEnable(s_app.address, true);
    case MOTOR_APP_DISABLE_ONLY: return ZDT_X42S_StartEnable(s_app.address, false);
    case MOTOR_APP_STATUS: return ZDT_X42S_StartStatusQuery(s_app.address);
    case MOTOR_APP_BASELINE: return ZDT_X42S_StartPositionQuery(s_app.address);
    case MOTOR_APP_MOVE_ACK: return ZDT_X42S_StartEmmMove(&s_app.move);
    case MOTOR_APP_QUERY: return ZDT_X42S_StartQuery(s_app.address, s_app.query_function);
    case MOTOR_APP_STOPPING: return ZDT_X42S_StartStop(s_app.address);
    default: return RS485_INVALID_PARAM;
    }
}
void MOTOR_AppProc(void)
{
    uint32_t now = HAL_GetTick();
    if (s_app.stage == MOTOR_APP_BOOT_WAIT) {
        if ((uint32_t)(now - boot_tick) >= MOTOR_STARTUP_MS) { Stage(MOTOR_APP_IDLE); }
        return;
    }
    if (!Active()) { return; }
    const ZDT_Debug_t *p = ZDT_X42S_PeekDebug();
    if (s_app.stage == MOTOR_APP_MONITOR) {
        switch (p->motion) {
        case ZDT_MOTION_ARRIVED: Stage(MOTOR_APP_DONE); break;
        case ZDT_MOTION_TIMEOUT: Fail(RS485_TIMEOUT, MOTOR_APP_NO_ARRIVAL); break;
        case ZDT_MOTION_REJECTED: Fail(RS485_NOT_READY, MOTOR_APP_REPLY_REJECTED); break;
        case ZDT_MOTION_FAULT: Fail(RS485_NOT_READY, MOTOR_APP_STALL); break;
        case ZDT_MOTION_UNKNOWN: Fail(p->comm_result, MOTOR_APP_IO_FAILURE); break;
        default: break;
        }
        return;
    }
    if (!s_app.waiting_for_reply) {
        if ((uint32_t)(now - last_step_tick) < MOTOR_FRAME_GAP_MS) { return; }
        RS485_Result_t result = SendStage();
        if (result == RS485_BUSY) {
            if ((uint32_t)(now - stage_tick) >= MOTOR_SEND_WAIT_MS) { Fail(RS485_TIMEOUT, MOTOR_APP_IO_FAILURE); }
            return; /* Command not started: no motor command is being retried. */
        }
        if (result != RS485_OK) {
            Fail(result, s_app.stage == MOTOR_APP_MOVE_ACK ? MOTOR_APP_MOVE_CHECK_FAILED : MOTOR_APP_IO_FAILURE);
            return;
        }
        s_app.waiting_for_reply = true;
        return;
    }
    if (p->transaction == ZDT_TXN_WAIT) { return; }
    if (p->transaction != ZDT_TXN_SUCCESS) {
        Fail(p->comm_result == RS485_OK ? RS485_NOT_READY : p->comm_result,
             p->transaction == ZDT_TXN_REJECTED ? MOTOR_APP_REPLY_REJECTED : MOTOR_APP_IO_FAILURE);
        return;
    }
    last_step_tick = now;
    switch (s_app.stage) {
    case MOTOR_APP_OPTIONS:
        if (!p->options_valid || (p->options & 0x86U) != 0x06U) {
            Fail(RS485_NOT_READY, MOTOR_APP_BAD_OPTIONS);
        } else { Stage(MOTOR_APP_ENABLE); }
        break;
    case MOTOR_APP_ENABLE: Stage(MOTOR_APP_STATUS); break;
    case MOTOR_APP_STATUS:
        if (!p->status_valid || !p->status.enabled) { Fail(RS485_NOT_READY, MOTOR_APP_DISABLED); }
        else if (p->status.stalled || p->status.stall_protection) { Fail(RS485_NOT_READY, MOTOR_APP_STALL); }
        else { Stage(MOTOR_APP_BASELINE); }
        break;
    case MOTOR_APP_BASELINE: Stage(MOTOR_APP_MOVE_ACK); break;
    case MOTOR_APP_MOVE_ACK:
        if (!p->command_accepted) { Fail(RS485_NOT_READY, MOTOR_APP_REPLY_REJECTED); }
        else { Stage(p->motion == ZDT_MOTION_ARRIVED ? MOTOR_APP_DONE : MOTOR_APP_MONITOR); }
        break;
    case MOTOR_APP_STOPPING: Stage(MOTOR_APP_STOP_ACCEPTED); break;
    default: Stage(MOTOR_APP_DONE); break;
    }
}
bool MOTOR_AppGetDebug(MOTOR_AppDebug_t *out)
{
    if (out == NULL) { return false; }
    *out = s_app;
    return true;
}
