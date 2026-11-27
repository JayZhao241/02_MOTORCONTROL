#include "zdt_x42s.h"
#include "stm32f4xx_hal.h"
#include <stddef.h>
#include <string.h>

#define RESPONSE_MS 100U
#define FRESH_MS 1000U
#define POLL_MS 50U
#define EMM_DEGREES_PER_UNIT (360.0 / 65536.0)
static ZDT_Debug_t s_query;
static uint8_t window[EMM_V5_MAX_REPLY], used;
static uint32_t txn_tick, motion_tick, motion_timeout, poll_tick, reply_tick;
static int64_t baseline;
static uint8_t poll_index;
static bool stop_requires_recovery;
static RS485_Result_t MoveResult(RS485_Result_t result, ZDT_MoveReject_t reason)
{
    s_query.move_reject = reason;
    s_query.last_start_result = result;
    return result;
}

bool ZDT_X42S_ParseAck(const uint8_t *f, uint16_t n, uint8_t addr,
                        uint8_t fn, uint8_t *out)
{
    if (!f || !out || !addr || n != 4U || f[0] != addr ||
        f[1] != fn || f[3] != 0x6BU || !EMM_V5_IsAckStatus(f[2])) { return false; }
    *out = f[2];
    return true;
}
bool ZDT_X42S_ParseVersion(const uint8_t *f, uint16_t n, uint8_t addr,
                            ZDT_X42S_Version_t *out)
{
    if (!f || !out || !addr || n != 7U || f[0] != addr ||
        f[1] != 0x1FU || f[6] != 0x6BU) { return false; }
    out->fw_version = (uint16_t)(((uint16_t)f[2] << 8) | f[3]);
    out->hw_series = f[4] >> 4;
    out->hw_type = f[4] & 0x0FU;
    out->hw_version = f[5];
    return true;
}
static bool Moving(void)
{
    return s_query.motion == ZDT_MOTION_WAIT_ACK || s_query.motion == ZDT_MOTION_ACCEPTED ||
           s_query.motion == ZDT_MOTION_MOVING;
}
static RS485_Result_t StartResult(RS485_Result_t r)
{
    s_query.last_start_result = r;
    return r;
}
static void SelectAddress(uint8_t address)
{
    if (s_query.address != address) {
        s_query.version_valid = s_query.status_valid = s_query.options_valid = s_query.position_valid = false;
        s_query.target_valid = s_query.voltage_valid = s_query.current_valid = false;
        s_query.health.valid = s_query.pid.valid = s_query.config.valid = false;
        s_query.integral_limit_valid = false;
        s_query.setpoint_valid = false;
        s_query.position_error_valid = false;
        s_query.home.valid = false;
        s_query.arrived = s_query.command_accepted = s_query.started_observed = false;
        s_query.state = ZDT_VERSION_IDLE;
    }
    s_query.address = address;
}
static RS485_Result_t Begin(const uint8_t *cmd, uint16_t len)
{
    if (s_query.transaction == ZDT_TXN_WAIT) { return StartResult(RS485_BUSY); }
    if (s_query.resync_required) { return StartResult(RS485_NOT_READY); }
    if (cmd[0] == 0U) { return StartResult(RS485_INVALID_PARAM); }
    if (Moving() && s_query.address != cmd[0]) { return StartResult(RS485_BUSY); }
    RS485_Result_t r = RS485_ClearRx();
    if (r != RS485_OK) { return StartResult(r); }
    used = 0U;
    s_query.rx_len = 0U;
    r = RS485_SendAsync(cmd, len);
    if (r != RS485_OK) { return StartResult(r); }
    SelectAddress(cmd[0]);
    s_query.function = cmd[1];
    s_query.reply_status_valid = false;
    s_query.comm_result = RS485_OK;
    s_query.transaction = ZDT_TXN_WAIT;
    txn_tick = HAL_GetTick();
    memcpy(s_query.last_tx, cmd, len);
    s_query.last_tx_len = len; s_query.last_tx_tick = txn_tick;
    if (s_query.function == 0x1FU) {
        s_query.version_valid = false;
        s_query.state = ZDT_VERSION_WAIT_RESPONSE;
    } else if (s_query.function == 0x3AU) { s_query.status_valid = false; }
    else if (s_query.function == 0x1AU) { s_query.options_valid = false; }
    else if (s_query.function == 0x36U) { s_query.position_valid = false; }
    else if (s_query.function == 0x37U) { s_query.position_error_valid = false; }
    else if (s_query.function == 0x33U) { s_query.target_valid = false; }
    else if (s_query.function == 0x34U) { s_query.setpoint_valid = false; }
    else if (s_query.function == 0x24U) { s_query.voltage_valid = false; }
    else if (s_query.function == 0x27U) { s_query.current_valid = false; }
    else if (s_query.function == 0xF3U) { s_query.status_valid = false; }
    else if (s_query.function == 0x3BU) { s_query.health.valid = false; }
    else if (s_query.function == 0x21U) { s_query.pid.valid = false; }
    else if (s_query.function == 0x42U) { s_query.config.valid = false; }
    else if (s_query.function == 0x23U) { s_query.integral_limit_valid = false; }
    if (s_query.function == 0x22U) { s_query.home.valid = false; }
    return StartResult(r);
}
RS485_Result_t ZDT_X42S_StartQuery(uint8_t addr, uint8_t fn)
{
    uint8_t cmd[4];
    if (!EMM_V5_BuildRead(addr, fn, cmd, sizeof(cmd))) { return StartResult(RS485_INVALID_PARAM); }
    if ((fn == 0x21U || fn == 0x42U) &&
        (s_query.address != addr || !s_query.options_valid || !(s_query.options & 2U))) {
        return StartResult(RS485_NOT_READY);
    }
    return Begin(cmd, fn == 0x42U ? 4U : 3U);
}
RS485_Result_t ZDT_X42S_WriteHome(uint8_t address, uint8_t function, const uint8_t *parameters)
{
    uint8_t frame[20];
    if (!EMM_V5_BuildHomeWrite(address, function, parameters, frame, sizeof(frame))) {
        return StartResult(RS485_INVALID_PARAM);
    }
    return Begin(frame, function == 0x4CU ? 20U : function == 0x93U ? 5U : 4U);
}
#define Query ZDT_X42S_StartQuery
RS485_Result_t ZDT_X42S_StartVersionQuery(uint8_t addr) { return Query(addr, 0x1FU); }
RS485_Result_t ZDT_X42S_RequestVersion(uint8_t addr) { return ZDT_X42S_StartVersionQuery(addr); }
RS485_Result_t ZDT_X42S_StartStatusQuery(uint8_t addr) { return Query(addr, 0x3AU); }
RS485_Result_t ZDT_X42S_StartOptionsQuery(uint8_t addr) { return Query(addr, 0x1AU); }
RS485_Result_t ZDT_X42S_StartPositionQuery(uint8_t addr) { return Query(addr, 0x36U); }
RS485_Result_t ZDT_X42S_StartVoltageQuery(uint8_t addr) { return Query(addr, 0x24U); }
RS485_Result_t ZDT_X42S_StartCurrentQuery(uint8_t addr) { return Query(addr, 0x27U); }
RS485_Result_t ZDT_X42S_StartTargetQuery(uint8_t addr) { return Query(addr, 0x33U); }

RS485_Result_t ZDT_X42S_StartEmmMove(const ZDT_EmmMove_t *m)
{
    uint8_t cmd[13];
    if (!EMM_V5_BuildPosition(m, cmd, sizeof(cmd))) {
        return MoveResult(RS485_INVALID_PARAM, ZDT_MOVE_INVALID_PARAM);
    }
    if (Moving() || s_query.transaction == ZDT_TXN_WAIT) {
        return MoveResult(RS485_BUSY, ZDT_MOVE_BUSY);
    }
    uint32_t now = HAL_GetTick();
    if (s_query.resync_required) { return MoveResult(RS485_NOT_READY, ZDT_MOVE_RESYNC_REQUIRED); }
    if (s_query.address != m->address) { return MoveResult(RS485_NOT_READY, ZDT_MOVE_ADDRESS_MISMATCH); }
    if (!s_query.options_valid || !s_query.status_valid || !s_query.position_valid) {
        return MoveResult(RS485_NOT_READY, ZDT_MOVE_MISSING_DATA);
    }
    if (now - s_query.status_tick > FRESH_MS || now - s_query.position_tick > FRESH_MS ||
        now - s_query.options_tick > FRESH_MS) { return MoveResult(RS485_NOT_READY, ZDT_MOVE_STALE_DATA); }
    if (!(s_query.options & 2U)) { return MoveResult(RS485_NOT_READY, ZDT_MOVE_NOT_EMM); }
    if (!(s_query.options & 4U)) { return MoveResult(RS485_NOT_READY, ZDT_MOVE_NOT_CLOSED_LOOP); }
    if (s_query.options & 128U) { return MoveResult(RS485_NOT_READY, ZDT_MOVE_INPUT_SCALED); }
    if (!s_query.status.enabled) { return MoveResult(RS485_NOT_READY, ZDT_MOVE_DISABLED); }
    if (s_query.status.stalled || s_query.status.stall_protection) {
        return MoveResult(RS485_NOT_READY, ZDT_MOVE_STALLED);
    }
    /* Prf_TF reports arrival at a target; it is not a general stationary
     * permission bit. Known terminal motions may start a new explicit move.
     * Moving()/unknown/fault guards provide the single-motion exclusion. */
    if (s_query.motion == ZDT_MOTION_UNKNOWN || s_query.motion == ZDT_MOTION_TIMEOUT ||
        s_query.motion == ZDT_MOTION_FAULT) { return MoveResult(RS485_NOT_READY, ZDT_MOVE_PREVIOUS_UNCERTAIN); }
    RS485_Result_t r = Begin(cmd, sizeof(cmd));
    s_query.move_reject = r == RS485_OK ? ZDT_MOVE_READY : ZDT_MOVE_SEND_FAILED;
    if (r == RS485_OK) {
        s_query.motion = ZDT_MOTION_WAIT_ACK;
        s_query.command_accepted = s_query.started_observed = s_query.arrived = s_query.saw_not_arrived = false;
        baseline = s_query.position;
        s_query.position_before_move = baseline;
        /* These snapshots describe the pre-command motor state. Keep the
         * baseline separately and require new readbacks for a later move. */
        s_query.status_valid = s_query.position_valid = false;
        s_query.position_error_valid = false;
        s_query.position_delta = 0;
        memcpy(s_query.move_frame, cmd, sizeof(cmd));
        memset(s_query.move_reply, 0, sizeof(s_query.move_reply));
        s_query.move_reply_valid = false;
        s_query.target_valid = s_query.voltage_valid = s_query.current_valid = false;
        s_query.setpoint_valid = false;
        motion_tick = now;
        motion_timeout = m->timeout_ms;
        poll_tick = now;
        poll_index = 0U;
    }
    return r;
}
RS485_Result_t ZDT_X42S_TestRelative(uint8_t addr, uint32_t dir,
                                   uint32_t pulses, uint32_t rpm)
{
    if (!pulses || pulses > 100U || !rpm || rpm > 10U) {
        return MoveResult(RS485_INVALID_PARAM, ZDT_MOVE_INVALID_PARAM);
    }
    const ZDT_EmmMove_t m = {addr, dir, rpm, 0U, pulses, 0U, 10000U};
    return ZDT_X42S_StartEmmMove(&m);
}
RS485_Result_t ZDT_X42S_StartEnable(uint8_t addr, bool enable)
{
    if (Moving()) { return StartResult(RS485_BUSY); }
    uint8_t cmd[6];
    if (!EMM_V5_BuildEnable(addr, enable, cmd, sizeof(cmd))) { return StartResult(RS485_INVALID_PARAM); }
    return Begin(cmd, sizeof(cmd));
}
RS485_Result_t ZDT_X42S_StartStop(uint8_t addr)
{
    uint8_t cmd[5];
    if (!EMM_V5_BuildStop(addr, cmd, sizeof(cmd))) { return StartResult(RS485_INVALID_PARAM); }
    if (RS485_IsTxBusy()) { return StartResult(RS485_BUSY); }
    if (Moving() && addr != s_query.address) { return StartResult(RS485_INVALID_PARAM); }
    RS485_Result_t result = RS485_SendAsync(cmd, sizeof(cmd));
    if (result != RS485_OK) { return StartResult(result); }
    stop_requires_recovery = s_query.resync_required || s_query.transaction == ZDT_TXN_WAIT;
    if (s_query.transaction == ZDT_TXN_WAIT) {
        s_query.cancelled_transactions++;
        if (s_query.function == 0x1FU) { s_query.state = ZDT_VERSION_ERROR; }
    }
    used = 0U; s_query.rx_len = 0U;
    SelectAddress(addr);
    s_query.status_valid = s_query.position_valid = s_query.target_valid = false;
    s_query.position_error_valid = false;
    s_query.setpoint_valid = false;
    s_query.function = 0xFEU;
    s_query.transaction = ZDT_TXN_WAIT; s_query.comm_result = RS485_OK;
    s_query.resync_required = false; /* Only FE completes this new transaction. */
    s_query.reply_status_valid = false;
    s_query.motion = ZDT_MOTION_STOP_REQUESTED; s_query.arrived = false;
    txn_tick = HAL_GetTick();
    memcpy(s_query.last_tx, cmd, sizeof(cmd));
    s_query.last_tx_len = sizeof(cmd); s_query.last_tx_tick = txn_tick;
    return StartResult(RS485_OK);
}
RS485_Result_t ZDT_X42S_ResumeAfterTimeout(void)
{
    if (s_query.transaction == ZDT_TXN_WAIT || Moving()) { return StartResult(RS485_BUSY); }
    RS485_Result_t r = RS485_ClearRx();
    if (r == RS485_OK) {
        memset(&s_query, 0, sizeof(s_query));
        used = 0U;
        s_query.state = ZDT_VERSION_IDLE;
    }
    return StartResult(r);
}
static void Fail(RS485_Result_t result)
{
    s_query.comm_result = result;
    s_query.transaction = result == RS485_TIMEOUT ? ZDT_TXN_TIMEOUT : ZDT_TXN_IO_ERROR;
    s_query.resync_required = true;
    s_query.status_valid = s_query.position_valid = s_query.options_valid = false;
    s_query.position_error_valid = false;
    if (s_query.function == 0x1FU) {
        s_query.state = result == RS485_TIMEOUT ? ZDT_VERSION_TIMEOUT : ZDT_VERSION_ERROR;
    }
    if (Moving() || s_query.motion == ZDT_MOTION_STOP_REQUESTED) { s_query.motion = ZDT_MOTION_UNKNOWN; }
    used = 0U;
}
static uint16_t ReadU16(const uint8_t *p)
{
    return (uint16_t)(((uint16_t)p[0] << 8) | p[1]);
}
static uint32_t ReadU32(const uint8_t *p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) | p[3];
}
static void AcceptFrame(const uint8_t *f, uint8_t n)
{
    memcpy(s_query.last_frame, f, n);
    s_query.last_frame_len = n;
    s_query.frames++;
    bool match = !s_query.resync_required && f[0] == s_query.address &&
                 f[1] == s_query.function && s_query.transaction == ZDT_TXN_WAIT;
    /* Completion notifications can arrive while a read-only query is pending. */
    if (!s_query.resync_required && f[0] == s_query.address && f[1] == 0xFDU && Moving()) {
        memcpy(s_query.move_reply, f, sizeof(s_query.move_reply));
        s_query.move_reply_valid = true;
        s_query.reply_status = f[2];
        s_query.reply_status_valid = true;
        if (f[2] == 0x02U || f[2] == 0x9FU) {
            s_query.command_accepted = true;
            s_query.motion = f[2] == 0x9FU ? ZDT_MOTION_ARRIVED : ZDT_MOTION_ACCEPTED;
            s_query.arrived = f[2] == 0x9FU;
        } else { s_query.motion = ZDT_MOTION_REJECTED; }
        if (!match) { return; }
    }
    if (!match) { s_query.ignored_frames++; return; }
    s_query.transaction = ZDT_TXN_SUCCESS;
    reply_tick = HAL_GetTick();
    s_query.last_reply_tick = reply_tick;
    if (f[1] == 0x1FU) {
        s_query.version_valid = ZDT_X42S_ParseVersion(f, n, s_query.address, &s_query.version);
        s_query.state = ZDT_VERSION_SUCCESS;
    } else if (f[1] == 0x3BU) {
        uint8_t b = f[2];
        s_query.health = (ZDT_Health_t){true, b, (b & 1U) != 0U, (b & 2U) != 0U,
            (b & 4U) != 0U, (b & 8U) != 0U, (b & 16U) != 0U, (b & 32U) != 0U};
    } else if (f[1] == 0x21U) {
        s_query.pid = (ZDT_EmmPID_t){true, ReadU32(f + 2), ReadU32(f + 6), ReadU32(f + 10)};
    } else if (f[1] == 0x23U) {
        s_query.integral_limit = ReadU32(f + 2);
        s_query.integral_limit_valid = true;
    } else if (f[1] == 0x42U) {
        ZDT_EmmConfig_t *c = &s_query.config;
        memcpy(c->raw, f, n);
        c->motor_type = f[4]; c->pulse_mode = f[5]; c->serial_mode = f[6];
        c->enable_level = f[7]; c->response_mode = f[22];
        c->microsteps = f[9] == 0U ? 256U : f[9];
        c->open_loop_current_ma = ReadU16(f + 12);
        c->max_current_ma = ReadU16(f + 14);
        c->max_output_raw = ReadU16(f + 16);
        c->arrival_window_tenths_deg = ReadU16(f + 30);
        c->valid = true;
    } else if (f[1] == 0x22U) {
        memcpy(s_query.home.raw, f+2, 15U);
        s_query.home.mode=f[2]; s_query.home.speed_rpm=ReadU16(f+4);
        s_query.home.timeout_ms=ReadU32(f+6); s_query.home.power_on=f[16];
        s_query.home.valid = f[2] <= 3U && f[3] <= 1U && f[16] <= 1U;
    } else if (f[1] == 0x1AU) {
        /* Actual motor reply: Addr 1A OptionsH OptionsL 6B (five bytes).
         * The manual's one-byte diagram omits the documented bits8/9. */
        s_query.options = (uint16_t)(((uint16_t)f[2] << 8) | f[3]);
        s_query.options_valid = true;
        s_query.options_tick = HAL_GetTick();
    } else if (f[1] == 0x24U || f[1] == 0x27U) {
        uint16_t value = (uint16_t)(((uint16_t)f[2] << 8) | f[3]);
        if (f[1] == 0x24U) { s_query.bus_voltage_mv = value; s_query.voltage_valid = true; }
        else { s_query.phase_current_ma = value; s_query.current_valid = true; }
    } else if (f[1] == 0x36U || f[1] == 0x33U || f[1] == 0x34U || f[1] == 0x37U) {
        uint32_t magnitude = ReadU32(f + 3);
        int64_t value = f[2] ? -(int64_t)magnitude : (int64_t)magnitude;
        if (f[1] == 0x33U) { s_query.target_position = value; s_query.target_valid = true; }
        else if (f[1] == 0x34U) { s_query.setpoint_position = value; s_query.setpoint_valid = true; }
        else if (f[1] == 0x37U) {
            s_query.position_error = value;
            s_query.position_error_deg = (double)value * EMM_DEGREES_PER_UNIT;
            s_query.position_error_valid = true;
        } else {
            s_query.position = value;
            s_query.position_deg = (double)value * EMM_DEGREES_PER_UNIT;
            s_query.position_valid = true; s_query.position_tick = HAL_GetTick();
            if (s_query.command_accepted) { s_query.position_delta = value - baseline; }
            if (Moving() && s_query.command_accepted &&
                (value - baseline > 16 || baseline - value > 16)) {
                s_query.started_observed = true;
                s_query.motion = ZDT_MOTION_MOVING;
            }
        }
    } else if (f[1] == 0x3AU) {
        uint8_t b = f[2];
        s_query.status = (ZDT_MotorStatus_t){b, (b & 1U) != 0U, (b & 2U) != 0U,
            (b & 4U) != 0U, (b & 8U) != 0U, (b & 16U) != 0U,
            (b & 32U) != 0U, (b & 128U) != 0U};
        s_query.status_valid = true; s_query.status_tick = HAL_GetTick();
        if (Moving() && s_query.command_accepted) {
            /* Small or fast moves can keep the arrival flag set between polls.
             * With no observed edge, require the post-command trajectory (34)
             * to have reached the final target (33). */
            bool trajectory_done = s_query.target_valid && s_query.setpoint_valid &&
                                   s_query.setpoint_position == s_query.target_position;
            if (!s_query.status.enabled || s_query.status.stalled || s_query.status.stall_protection) {
                s_query.motion = ZDT_MOTION_FAULT;
            } else if (!s_query.status.arrived) {
                s_query.saw_not_arrived = true;
                /* Not at target is not proof that the rotor is moving. */
                s_query.motion = s_query.started_observed ? ZDT_MOTION_MOVING : ZDT_MOTION_ACCEPTED;
            } else if (s_query.saw_not_arrived || trajectory_done) {
                s_query.motion = ZDT_MOTION_ARRIVED; s_query.arrived = true;
            }
        }
    } else {
        s_query.reply_status = f[2]; s_query.reply_status_valid = true;
        if (f[2] != 0x02U && f[2] != 0x9FU) { s_query.transaction = ZDT_TXN_REJECTED; }
        if (f[1] == 0xFEU) {
            if (s_query.transaction == ZDT_TXN_SUCCESS) {
                s_query.motion = ZDT_MOTION_IDLE;
                s_query.resync_required = stop_requires_recovery;
            } else { s_query.motion = ZDT_MOTION_UNKNOWN; s_query.resync_required = true; }
        }
    }
}
/* Keep the earliest plausible frame until its fixed length is available.
 * Its payload can contain an entire ACK-shaped sequence (including FD 9F).
 * Only slide past an invalid header/frame; an incomplete candidate delays
 * resynchronization by at most EMM_V5_MAX_REPLY bytes. Fixed 6B is not a CRC. */
static void Feed(uint8_t byte)
{
    if (used == sizeof(window)) { memmove(window, window + 1, --used); }
    window[used++] = byte;
    while (used >= 2U) {
        const uint8_t *f = window;
        uint8_t len = EMM_V5_ReplyLength(f[1]);
        bool invalid = !f[0] || len == 0U;
        if (!invalid && used >= 3U) {
            invalid = (EMM_V5_IsAckFunction(f[1]) && !EMM_V5_IsAckStatus(f[2])) ||
                      ((f[1] == 0x36U || f[1] == 0x33U || f[1] == 0x34U || f[1] == 0x37U) && f[2] > 1U);
            if (f[1] == 0x42U && f[2] != 33U) { invalid = true; }
        }
        if (!invalid && f[1] == 0x42U && used >= 4U && f[3] != 21U) { invalid = true; }
        if (!invalid && used < len) { break; }
        uint8_t consumed = 1U;
        if (!invalid && f[len - 1U] == 0x6BU) {
            AcceptFrame(f, len);
            consumed = len;
        }
        used -= consumed;
        memmove(window, window + consumed, used);
    }
    s_query.rx_len = s_query.transaction == ZDT_TXN_WAIT ? used : s_query.last_frame_len;
}
void ZDT_X42S_Proc(void)
{
    uint32_t now = HAL_GetTick();
    if (s_query.transaction == ZDT_TXN_WAIT && now - txn_tick >=
        ((s_query.function == 0x93U || s_query.function == 0x4CU) ? 1000U : RESPONSE_MS)) {
        Fail(RS485_TIMEOUT);
    }
    /* Only this location reads RX, including idle/late notifications. */
    uint8_t bytes[32]; uint16_t count = 0U;
    RS485_Result_t r = RS485_Read(bytes, sizeof(bytes), &count);
    if (r != RS485_OK) {
        if (!s_query.resync_required) { Fail(r); }
        return;
    }
    for (uint16_t i = 0U; i < count; i++) { Feed(bytes[i]); }
    now = HAL_GetTick();
    if (Moving() && now - motion_tick >= motion_timeout) {
        s_query.motion = ZDT_MOTION_TIMEOUT;
        s_query.resync_required = true;
        s_query.arrived = false;
    }
    if (Moving() && s_query.command_accepted && s_query.transaction != ZDT_TXN_WAIT &&
        !s_query.resync_required && now - poll_tick >= POLL_MS && now - reply_tick >= POLL_MS) {
        /* Leave the completed transaction visible to the application for at
         * least one iteration, even when its response took over POLL_MS. */
        /* Read-only diagnostics distinguish an accepted target from actual
         * displacement and an unpowered/current-limited output stage. */
        static const uint8_t functions[] = {0x3AU, 0x36U, 0x33U, 0x24U, 0x27U, 0x34U};
        r = Query(s_query.address, functions[poll_index]);
        if (r == RS485_OK) {
            poll_index = (uint8_t)((poll_index + 1U) % sizeof(functions));
            poll_tick = now;
        }
    }
}
bool ZDT_X42S_GetDebug(ZDT_Debug_t *out)
{ if (!out) { return false; } *out = s_query; return true; }
bool ZDT_X42S_GetStatus(ZDT_MotorStatus_t *out)
{ if (!out || !s_query.status_valid) { return false; } *out = s_query.status; return true; }
bool ZDT_X42S_GetVersion(ZDT_X42S_Version_t *out)
{ if (!out || !s_query.version_valid) { return false; } *out = s_query.version; return true; }
ZDT_VersionState_t ZDT_X42S_GetVersionState(void) { return s_query.state; }
RS485_Result_t ZDT_X42S_GetCommResult(void) { return s_query.comm_result; }

const ZDT_Debug_t *ZDT_X42S_PeekDebug(void) { return &s_query; }
