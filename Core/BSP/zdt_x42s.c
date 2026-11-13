#include "zdt_x42s.h"
#include "stm32f4xx_hal.h"
#include <stddef.h>
#include <string.h>

#define RESPONSE_MS 100U
#define FRESH_MS 1000U
#define POLL_MS 50U
static ZDT_Debug_t s_query;
static uint8_t window[8], used;
static uint32_t txn_tick, motion_tick, motion_timeout, poll_tick;
static int64_t baseline;
static bool poll_position;

static bool AckStatus(uint8_t value)
{
    return value == 0x02U || value == 0xE2U || value == 0xEEU ||
           value == 0x9FU || value == 0x12U || value == 0x22U;
}
bool ZDT_X42S_ParseAck(const uint8_t *f, uint16_t n, uint8_t addr,
                        uint8_t fn, uint8_t *out)
{
    if (!f || !out || !addr || n != 4U || f[0] != addr ||
        f[1] != fn || f[3] != 0x6BU || !AckStatus(f[2])) { return false; }
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
    if (s_query.address != cmd[0]) {
        s_query.version_valid = s_query.status_valid = s_query.options_valid = s_query.position_valid = false;
        s_query.arrived = s_query.command_accepted = s_query.started_observed = false;
        s_query.state = ZDT_VERSION_IDLE;
    }
    s_query.address = cmd[0];
    s_query.function = cmd[1];
    s_query.reply_status_valid = false;
    s_query.comm_result = RS485_OK;
    s_query.transaction = ZDT_TXN_WAIT;
    txn_tick = HAL_GetTick();
    if (s_query.function == 0x1FU) {
        s_query.version_valid = false;
        s_query.state = ZDT_VERSION_WAIT_RESPONSE;
    } else if (s_query.function == 0x3AU) { s_query.status_valid = false; }
    else if (s_query.function == 0x1AU) { s_query.options_valid = false; }
    else if (s_query.function == 0x36U) { s_query.position_valid = false; }
    return StartResult(r);
}
static RS485_Result_t Query(uint8_t addr, uint8_t fn)
{
    const uint8_t cmd[] = {addr, fn, 0x6BU};
    return Begin(cmd, sizeof(cmd));
}
RS485_Result_t ZDT_X42S_StartVersionQuery(uint8_t addr) { return Query(addr, 0x1FU); }
RS485_Result_t ZDT_X42S_RequestVersion(uint8_t addr) { return ZDT_X42S_StartVersionQuery(addr); }
RS485_Result_t ZDT_X42S_StartStatusQuery(uint8_t addr) { return Query(addr, 0x3AU); }
RS485_Result_t ZDT_X42S_StartOptionsQuery(uint8_t addr) { return Query(addr, 0x1AU); }
RS485_Result_t ZDT_X42S_StartPositionQuery(uint8_t addr) { return Query(addr, 0x36U); }

RS485_Result_t ZDT_X42S_StartEmmMove(const ZDT_EmmMove_t *m)
{
    if (!m || !m->address || m->direction > 1U || m->speed_rpm == 0U ||
        m->speed_rpm > 3000U || m->acceleration > 255U || !m->pulses ||
        m->mode > 2U || m->timeout_ms < 100U || m->timeout_ms > 120000U) {
        return StartResult(RS485_INVALID_PARAM);
    }
    if (Moving() || s_query.transaction == ZDT_TXN_WAIT) { return StartResult(RS485_BUSY); }
    uint32_t now = HAL_GetTick();
    if (s_query.resync_required || s_query.address != m->address || !s_query.options_valid ||
        !s_query.status_valid || !s_query.position_valid || (s_query.options & 0x86U) != 0x06U ||
        now - s_query.status_tick > FRESH_MS || now - s_query.position_tick > FRESH_MS ||
        now - s_query.options_tick > FRESH_MS || !s_query.status.enabled || !s_query.status.arrived ||
        s_query.status.stalled || s_query.status.stall_protection ||
        s_query.motion == ZDT_MOTION_UNKNOWN || s_query.motion == ZDT_MOTION_TIMEOUT ||
        s_query.motion == ZDT_MOTION_FAULT) { return StartResult(RS485_NOT_READY); }
    const uint8_t cmd[] = {
        m->address, 0xFDU, (uint8_t)m->direction,
        (uint8_t)(m->speed_rpm >> 8), (uint8_t)m->speed_rpm,
        (uint8_t)m->acceleration, (uint8_t)(m->pulses >> 24),
        (uint8_t)(m->pulses >> 16), (uint8_t)(m->pulses >> 8), (uint8_t)m->pulses,
        (uint8_t)m->mode, 0x00U, 0x6BU
    };
    RS485_Result_t r = Begin(cmd, sizeof(cmd));
    if (r == RS485_OK) {
        s_query.motion = ZDT_MOTION_WAIT_ACK;
        s_query.command_accepted = s_query.started_observed = s_query.arrived = s_query.saw_not_arrived = false;
        baseline = s_query.position;
        motion_tick = now;
        motion_timeout = m->timeout_ms;
        poll_tick = now;
        poll_position = false;
    }
    return r;
}
RS485_Result_t ZDT_X42S_TestRelative(uint8_t addr, uint32_t dir,
                                   uint32_t pulses, uint32_t rpm)
{
    if (!pulses || pulses > 100U || !rpm || rpm > 10U) {
        return StartResult(RS485_INVALID_PARAM);
    }
    const ZDT_EmmMove_t m = {addr, dir, rpm, 10U, pulses, 2U, 10000U};
    return ZDT_X42S_StartEmmMove(&m);
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
    if (s_query.function == 0x1FU) {
        s_query.state = result == RS485_TIMEOUT ? ZDT_VERSION_TIMEOUT : ZDT_VERSION_ERROR;
    }
    if (Moving()) { s_query.motion = ZDT_MOTION_UNKNOWN; }
    used = 0U;
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
    if (f[1] == 0x1FU) {
        s_query.version_valid = ZDT_X42S_ParseVersion(f, n, s_query.address, &s_query.version);
        s_query.state = ZDT_VERSION_SUCCESS;
    } else if (f[1] == 0x1AU) {
        s_query.options = f[2]; s_query.options_valid = true; s_query.options_tick = HAL_GetTick();
    } else if (f[1] == 0x36U) {
        uint32_t magnitude = ((uint32_t)f[3] << 24) | ((uint32_t)f[4] << 16) |
                             ((uint32_t)f[5] << 8) | f[6];
        s_query.position = f[2] ? -(int64_t)magnitude : (int64_t)magnitude;
        s_query.position_valid = true; s_query.position_tick = HAL_GetTick();
        if (Moving() && s_query.command_accepted &&
            (s_query.position - baseline > 16 || baseline - s_query.position > 16)) {
            s_query.started_observed = true; /* Exceeds encoder jitter, not a target check. */
        }
    } else if (f[1] == 0x3AU) {
        uint8_t b = f[2];
        s_query.status = (ZDT_MotorStatus_t){b, (b & 1U) != 0U, (b & 2U) != 0U,
            (b & 4U) != 0U, (b & 8U) != 0U, (b & 16U) != 0U,
            (b & 32U) != 0U, (b & 128U) != 0U};
        s_query.status_valid = true; s_query.status_tick = HAL_GetTick();
        if (Moving() && s_query.command_accepted) {
            if (!s_query.status.enabled || s_query.status.stalled || s_query.status.stall_protection) {
                s_query.motion = ZDT_MOTION_FAULT;
            } else if (!s_query.status.arrived) {
                s_query.saw_not_arrived = true;
                s_query.motion = ZDT_MOTION_MOVING;
            } else if (s_query.saw_not_arrived) {
                s_query.motion = ZDT_MOTION_ARRIVED; s_query.arrived = true;
            }
        }
    } else {
        s_query.reply_status = f[2]; s_query.reply_status_valid = true;
        if (f[2] != 0x02U && f[2] != 0x9FU) { s_query.transaction = ZDT_TXN_REJECTED; }
    }
}
/* Fixed lengths by function, never treat status data (e.g. 3A 02) as ACK.
 * Search every suffix so a false incomplete header cannot hide a valid frame.
 * Fixed 6B has no CRC: pathological noise can still resemble a valid reply. */
static void Feed(uint8_t byte)
{
    if (used == sizeof(window)) { memmove(window, window + 1, --used); }
    window[used++] = byte;
    for (uint8_t i = 0U; i + 4U <= used; i++) {
        const uint8_t *f = window + i;
        if (!f[0]) { continue; }
        uint8_t len;
        switch (f[1]) {
        case 0x1F: len = 7U; break;
        case 0x36: len = 8U; break;
        case 0x1A: case 0x3A: len = 4U; break;
        default:
            if (!AckStatus(f[2])) { continue; }
            len = 4U; break;
        }
        if (i + len > used || f[len - 1U] != 0x6BU ||
            (f[1] == 0x36U && f[2] > 1U)) { continue; }
        AcceptFrame(f, len);
        uint8_t consumed = i + len;
        used -= consumed;
        memmove(window, window + consumed, used);
        break;
    }
    s_query.rx_len = s_query.transaction == ZDT_TXN_WAIT ? used : s_query.last_frame_len;
}
void ZDT_X42S_Proc(void)
{
    uint32_t now = HAL_GetTick();
    if (s_query.transaction == ZDT_TXN_WAIT && now - txn_tick >= RESPONSE_MS) {
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
    if (Moving() && now - motion_tick >= motion_timeout) {
        s_query.motion = ZDT_MOTION_TIMEOUT;
        s_query.resync_required = true;
        s_query.arrived = false;
    }
    if (Moving() && s_query.command_accepted && s_query.transaction != ZDT_TXN_WAIT &&
        !s_query.resync_required && now - poll_tick >= POLL_MS) {
        r = Query(s_query.address, poll_position ? 0x36U : 0x3AU);
        if (r == RS485_OK) { poll_position = !poll_position; poll_tick = now; }
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
