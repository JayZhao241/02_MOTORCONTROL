#ifndef ZDT_X42S_H
#define ZDT_X42S_H
#include "rs485.h"

typedef struct {
    uint16_t fw_version;
    uint8_t hw_series, hw_type, hw_version;
} ZDT_X42S_Version_t;
typedef enum {
    ZDT_VERSION_IDLE = 0, ZDT_VERSION_WAIT_RESPONSE, ZDT_VERSION_SUCCESS,
    ZDT_VERSION_TIMEOUT, ZDT_VERSION_ERROR
} ZDT_VersionState_t;
typedef enum {
    ZDT_TXN_IDLE = 0, ZDT_TXN_WAIT, ZDT_TXN_SUCCESS, ZDT_TXN_REJECTED,
    ZDT_TXN_TIMEOUT, ZDT_TXN_IO_ERROR
} ZDT_TransactionState_t;
typedef enum {
    ZDT_MOTION_IDLE = 0, ZDT_MOTION_WAIT_ACK, ZDT_MOTION_ACCEPTED,
    ZDT_MOTION_MOVING, ZDT_MOTION_ARRIVED, ZDT_MOTION_REJECTED,
    ZDT_MOTION_UNKNOWN, ZDT_MOTION_FAULT, ZDT_MOTION_TIMEOUT
} ZDT_MotionState_t;
typedef struct {
    uint8_t raw;
    bool enabled, arrived, stalled, stall_protection;
    bool left_limit_level, right_limit_level, power_marker;
} ZDT_MotorStatus_t;
/* Emm only, speed in RPM, acceleration in firmware levels (not RPM/s).
 * mode: 0 previous target relative, 1 absolute, 2 current position relative.
 * Position is in INPUT PULSES, never assumed to be degrees. Sync is always 0. */
typedef struct {
    uint8_t address;
    uint32_t direction, speed_rpm, acceleration, pulses, mode, timeout_ms;
} ZDT_EmmMove_t;
typedef struct {
    /* Legacy Watch names: state is the version-query state; rx_len is the
     * current candidate length while waiting, or the last frame length. */
    ZDT_VersionState_t state;
    uint16_t rx_len;
    ZDT_TransactionState_t transaction;
    RS485_Result_t comm_result, last_start_result;
    uint8_t address, function, reply_status;
    bool reply_status_valid, resync_required;
    uint8_t last_frame[8];
    uint16_t last_frame_len;
    uint32_t frames, ignored_frames;
    ZDT_X42S_Version_t version;
    ZDT_MotorStatus_t status;
    bool version_valid, status_valid, options_valid, position_valid;
    uint8_t options; /* bit1 Emm, bit7 input scaling; raw one-byte table. */
    int64_t position; /* signed encoder units; Emm 65536 units/revolution */
    uint32_t status_tick, position_tick, options_tick;
    ZDT_MotionState_t motion;
    bool command_accepted, started_observed, arrived, saw_not_arrived;
} ZDT_Debug_t;

/* All APIs run in the main loop; only this module calls RS485_Read. No broadcast. */
bool ZDT_X42S_ParseVersion(const uint8_t *frame, uint16_t length,
                            uint8_t address, ZDT_X42S_Version_t *out);
bool ZDT_X42S_ParseAck(const uint8_t *frame, uint16_t length,
                        uint8_t address, uint8_t function, uint8_t *status);
RS485_Result_t ZDT_X42S_StartVersionQuery(uint8_t address);
/* Compatibility alias now starts a managed transaction, never a bare send. */
RS485_Result_t ZDT_X42S_RequestVersion(uint8_t address);
RS485_Result_t ZDT_X42S_StartStatusQuery(uint8_t address);
RS485_Result_t ZDT_X42S_StartOptionsQuery(uint8_t address);
RS485_Result_t ZDT_X42S_StartPositionQuery(uint8_t address);
/* Requires fresh status/position/options from this address, Emm closed loop,
 * no input scaling, enabled, no stall, previous target arrived. Returns the
 * actual send-start result; check transaction/motion for the motor outcome. */
RS485_Result_t ZDT_X42S_StartEmmMove(const ZDT_EmmMove_t *move);
/* Explicit bench trigger: <=10 RPM, 1..100 input pulses, acceleration level 10,
 * relative to current position. Caller must confirm microstep/angle mapping. */
RS485_Result_t ZDT_X42S_TestRelative(uint8_t address, uint32_t direction,
                                   uint32_t pulses, uint32_t speed_rpm);
/* Only after operator has established a clean session (e.g. stopped/reset motor).
 * Discards published software bytes; cannot identify/eliminate future late data.
 * Invalidates all cached data and motion evidence. Never sends a motor command. */
RS485_Result_t ZDT_X42S_ResumeAfterTimeout(void);
void ZDT_X42S_Proc(void);
bool ZDT_X42S_GetDebug(ZDT_Debug_t *out);
bool ZDT_X42S_GetStatus(ZDT_MotorStatus_t *out);
bool ZDT_X42S_GetVersion(ZDT_X42S_Version_t *out);
ZDT_VersionState_t ZDT_X42S_GetVersionState(void);
RS485_Result_t ZDT_X42S_GetCommResult(void);
#endif
