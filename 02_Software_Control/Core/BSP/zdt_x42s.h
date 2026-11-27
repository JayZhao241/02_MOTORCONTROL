#ifndef ZDT_X42S_H
#define ZDT_X42S_H
#include "rs485.h"
#include "emm_v5.h"

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
    ZDT_MOTION_UNKNOWN, ZDT_MOTION_FAULT, ZDT_MOTION_TIMEOUT, ZDT_MOTION_STOP_REQUESTED
} ZDT_MotionState_t;
typedef struct {
    uint8_t raw;
    bool enabled, arrived, stalled, stall_protection;
    bool left_limit_level, right_limit_level, power_marker;
} ZDT_MotorStatus_t;
typedef EMM_V5_Position_t ZDT_EmmMove_t;
typedef struct {
    bool valid;
    uint8_t raw;
    bool encoder_ready, calibrated, homing, homing_failed, over_temperature, over_current;
} ZDT_Health_t;
typedef struct {
    bool valid;
    uint32_t kp, ki, kd;
} ZDT_EmmPID_t;
typedef struct {
    bool valid;
    uint8_t raw[EMM_V5_MAX_REPLY];
    uint8_t motor_type, pulse_mode, serial_mode, enable_level, response_mode;
    uint16_t microsteps, open_loop_current_ma, max_current_ma;
    uint16_t max_output_raw; /* Manual's unit multipliers conflict; retain raw. */
    uint16_t arrival_window_tenths_deg;
} ZDT_EmmConfig_t;
typedef enum {
    ZDT_MOVE_READY = 0, ZDT_MOVE_INVALID_PARAM, ZDT_MOVE_BUSY,
    ZDT_MOVE_RESYNC_REQUIRED, ZDT_MOVE_ADDRESS_MISMATCH, ZDT_MOVE_MISSING_DATA,
    ZDT_MOVE_STALE_DATA, ZDT_MOVE_NOT_EMM, ZDT_MOVE_NOT_CLOSED_LOOP,
    ZDT_MOVE_INPUT_SCALED, ZDT_MOVE_DISABLED, ZDT_MOVE_NOT_AT_REST,
    ZDT_MOVE_STALLED, ZDT_MOVE_PREVIOUS_UNCERTAIN, ZDT_MOVE_SEND_FAILED
} ZDT_MoveReject_t;
typedef struct {
    /* Legacy Watch names: state is the version-query state; rx_len is the
     * current candidate length while waiting, or the last frame length. */
    ZDT_VersionState_t state;
    uint16_t rx_len;
    ZDT_TransactionState_t transaction;
    RS485_Result_t comm_result, last_start_result;
    uint8_t address, function, reply_status;
    bool reply_status_valid, resync_required;
    uint8_t last_frame[EMM_V5_MAX_REPLY];
    uint16_t last_frame_len;
    uint32_t frames, ignored_frames;
    ZDT_X42S_Version_t version;
    ZDT_MotorStatus_t status;
    bool version_valid, status_valid, options_valid, position_valid;
    uint16_t options; /* 1A reply: big-endian 16 bits, including lock bits8/9. */
    int64_t position; /* signed encoder units; Emm 65536 units/revolution */
    uint32_t status_tick, position_tick, options_tick;
    ZDT_MoveReject_t move_reject;
    uint8_t last_tx[EMM_V5_MAX_FRAME];
    uint16_t last_tx_len;
    uint32_t last_tx_tick, last_reply_tick, cancelled_transactions;
    uint8_t move_frame[13], move_reply[4];
    bool move_reply_valid;
    int64_t position_before_move, position_delta, target_position;
    uint16_t bus_voltage_mv, phase_current_ma;
    bool target_valid, voltage_valid, current_valid;
    ZDT_MotionState_t motion;
    bool command_accepted, started_observed, arrived, saw_not_arrived;
    /* Explicit read-only diagnostics; never write PID/configuration or calibrate. */
    ZDT_Health_t health;
    ZDT_EmmPID_t pid;
    ZDT_EmmConfig_t config;
    bool integral_limit_valid;
    uint32_t integral_limit;
    int64_t setpoint_position; /* 34: trajectory position, distinct from 33 final target. */
    bool setpoint_valid;
    int64_t position_error; /* 37: signed Emm angle units, same scale as position. */
    double position_deg, position_error_deg;
    bool position_error_valid;
    struct {
        bool valid;
        uint8_t raw[15], mode, power_on;
        uint16_t speed_rpm;
        uint32_t timeout_ms;
    } home;
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
RS485_Result_t ZDT_X42S_StartVoltageQuery(uint8_t address);
RS485_Result_t ZDT_X42S_StartCurrentQuery(uint8_t address);
RS485_Result_t ZDT_X42S_StartTargetQuery(uint8_t address);
/* Requires fresh status/position/options from this address, Emm closed loop,
 * no input scaling, enabled, no stall and no active/uncertain previous move.
 * Arrival is an output observation, not a prerequisite. Returns the
 * actual send-start result; check transaction/motion for the motor outcome. */
RS485_Result_t ZDT_X42S_StartEmmMove(const ZDT_EmmMove_t *move);
/* Explicit bench trigger: <=10 RPM, 1..100 input pulses, acceleration 0,
 * relative to previous target (vendor example defaults). Caller must confirm microstep/angle mapping. */
RS485_Result_t ZDT_X42S_TestRelative(uint8_t address, uint32_t direction,
                                   uint32_t pulses, uint32_t speed_rpm);
/* Application workflow lives in motor_app.c. Enable never runs at boot. */
RS485_Result_t ZDT_X42S_StartEnable(uint8_t address, bool enable);
/* Priority stop: replaces the old logical transaction after UART TX ends.
 * Acknowledgement is acceptance, not proof of a stationary rotor. */
RS485_Result_t ZDT_X42S_StartStop(uint8_t address);
RS485_Result_t ZDT_X42S_StartQuery(uint8_t address, uint8_t function);
/* Additional reads: 3B health, 23 integral limit, 21 Emm PID, 42 Emm config.
 * 21/42 require valid Emm options from the same address first. */
/* Only after operator has established a clean session (e.g. stopped/reset motor).
 * Discards published software bytes; cannot identify/eliminate future late data.
 * Invalidates all cached data and motion evidence. Never sends a motor command. */
RS485_Result_t ZDT_X42S_ResumeAfterTimeout(void);
RS485_Result_t ZDT_X42S_WriteHome(uint8_t address, uint8_t function, const uint8_t *parameters);
void ZDT_X42S_Proc(void);
bool ZDT_X42S_GetDebug(ZDT_Debug_t *out);
/* Main-loop read-only view; no copy in the per-iteration application scheduler.
 * Do not retain across a ZDT operation or access from an ISR. */
const ZDT_Debug_t *ZDT_X42S_PeekDebug(void);
bool ZDT_X42S_GetStatus(ZDT_MotorStatus_t *out);
bool ZDT_X42S_GetVersion(ZDT_X42S_Version_t *out);
ZDT_VersionState_t ZDT_X42S_GetVersionState(void);
RS485_Result_t ZDT_X42S_GetCommResult(void);
#endif
