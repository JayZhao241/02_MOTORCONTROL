#ifndef MOTOR_APP_H
#define MOTOR_APP_H
#include "zdt_x42s.h"

typedef enum {
    MOTOR_APP_BOOT_WAIT = 0, MOTOR_APP_IDLE, MOTOR_APP_OPTIONS,
    MOTOR_APP_ENABLE, MOTOR_APP_STATUS, MOTOR_APP_BASELINE,
    MOTOR_APP_MOVE_ACK, MOTOR_APP_MONITOR, MOTOR_APP_QUERY,
    MOTOR_APP_STOPPING, MOTOR_APP_ENABLE_ONLY, MOTOR_APP_DISABLE_ONLY,
    MOTOR_APP_DONE, MOTOR_APP_STOP_ACCEPTED, MOTOR_APP_FAILED,
    MOTOR_APP_HOME_OPTIONS, MOTOR_APP_HOME_READ, MOTOR_APP_HOME_SAVE,
    MOTOR_APP_HOME_CLEAR, MOTOR_APP_HOME_WRITE, MOTOR_APP_HOME_VERIFY
} MOTOR_AppStage_t;
typedef enum {
    MOTOR_APP_NO_ERROR = 0, MOTOR_APP_BAD_OPTIONS, MOTOR_APP_DISABLED,
    MOTOR_APP_STALL, MOTOR_APP_REPLY_REJECTED, MOTOR_APP_IO_FAILURE,
    MOTOR_APP_MOVE_CHECK_FAILED, MOTOR_APP_NO_ARRIVAL, MOTOR_APP_CANCELLED
} MOTOR_AppError_t;
typedef struct {
    MOTOR_AppStage_t stage, failed_stage;
    MOTOR_AppError_t error;
    RS485_Result_t last_request_result; /* New request only: BUSY never replaces result. */
    RS485_Result_t result;              /* The operation currently running/last completed. */
    bool active, waiting_for_reply;
    uint8_t address, query_function;
    ZDT_EmmMove_t move;                 /* Captured once, never changed by later key presses. */
    uint32_t accepted_requests, rejected_requests, cancelled_requests;
} MOTOR_AppDebug_t;

/* Main loop only. Init starts a 500ms nonblocking readiness delay and sends
 * NOTHING. All motor operations require one explicit application request.
 * During operation no module other than motor_app should start ZDT transactions. */
void MOTOR_AppInit(void);
void MOTOR_AppProc(void);
RS485_Result_t MOTOR_AppMove(const ZDT_EmmMove_t *move);
RS485_Result_t MOTOR_AppQuery(uint8_t address, uint8_t function);
RS485_Result_t MOTOR_AppEnable(uint8_t address, bool enable);
RS485_Result_t MOTOR_AppStop(uint8_t address);
/* Operator confirms previous motor action is stopped and old session is clean.
 * Does not automatically restart DMA, enable, recalibrate, or retry motion. */
RS485_Result_t MOTOR_AppRecover(void);
RS485_Result_t MOTOR_AppSaveStartupOrigin(uint8_t address);
bool MOTOR_AppGetDebug(MOTOR_AppDebug_t *out);
#endif
