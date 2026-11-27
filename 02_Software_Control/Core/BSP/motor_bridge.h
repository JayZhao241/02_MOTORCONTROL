#ifndef MOTOR_BRIDGE_H
#define MOTOR_BRIDGE_H
#include "rs485.h"
/* Exclusive desktop bus owner. Requests: 1 acquire, 2 exchange, 3 release.
 * Host publishes request last with arm=0x4455414C on every request. */
typedef struct {
    uint32_t request, arm, tx_len, rx_len, result, active;
    uint8_t tx[20], rx[128];
    uint32_t driver_state, hal_error, hal_status, driver_error, tx_result;
    uint32_t stop_requested_mask, stop_failed_mask;
} MOTOR_Bridge_t;
extern volatile MOTOR_Bridge_t s_bridge;
bool MOTOR_BridgeProc(bool legacy_busy, bool stop_key);
#endif
