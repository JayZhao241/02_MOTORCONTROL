#ifndef EMM_V5_H
#define EMM_V5_H
#include <stdbool.h>
#include <stdint.h>

/* Byte layouts ported from supplied HAL Emm_V5.c, USART examples.
 * No UART/HAL calls here. Bounds follow the X42S manual's conservative 3000RPM
 * limit; the supplied example's comment says 5000RPM and is not used as a limit. */
#define EMM_V5_MAX_FRAME 20U
#define EMM_V5_MAX_REPLY 33U
typedef struct {
    uint8_t address;
    uint32_t direction, speed_rpm, acceleration, pulses, mode, timeout_ms;
} EMM_V5_Position_t;
bool EMM_V5_BuildPosition(const EMM_V5_Position_t *move, uint8_t *out, uint16_t capacity);
bool EMM_V5_BuildEnable(uint8_t address, bool enable, uint8_t *out, uint16_t capacity);
bool EMM_V5_BuildStop(uint8_t address, uint8_t *out, uint16_t capacity);
bool EMM_V5_BuildRead(uint8_t address, uint8_t function, uint8_t *out, uint16_t capacity);
bool EMM_V5_BuildHomeWrite(uint8_t address, uint8_t function, const uint8_t *parameters,
                           uint8_t *out, uint16_t capacity);
bool EMM_V5_IsAckFunction(uint8_t function);
uint8_t EMM_V5_ReplyLength(uint8_t function);
bool EMM_V5_IsAckStatus(uint8_t status);
#endif
