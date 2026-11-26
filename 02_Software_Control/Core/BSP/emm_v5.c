#include "emm_v5.h"
#include <stddef.h>

bool EMM_V5_BuildPosition(const EMM_V5_Position_t *m, uint8_t *out, uint16_t capacity)
{
    if (m == NULL || out == NULL || capacity < 13U || m->address == 0U ||
        m->direction > 1U || m->speed_rpm == 0U || m->speed_rpm > 3000U ||
        m->acceleration > 255U || m->pulses == 0U || m->mode > 2U ||
        m->timeout_ms < 100U || m->timeout_ms > 120000U) { return false; }
    out[0] = m->address; out[1] = 0xFDU; out[2] = (uint8_t)m->direction;
    out[3] = (uint8_t)(m->speed_rpm >> 8); out[4] = (uint8_t)m->speed_rpm;
    out[5] = (uint8_t)m->acceleration;
    out[6] = (uint8_t)(m->pulses >> 24); out[7] = (uint8_t)(m->pulses >> 16);
    out[8] = (uint8_t)(m->pulses >> 8); out[9] = (uint8_t)m->pulses;
    out[10] = (uint8_t)m->mode; out[11] = 0U; out[12] = 0x6BU;
    return true;
}
bool EMM_V5_BuildEnable(uint8_t address, bool enable, uint8_t *out, uint16_t capacity)
{
    if (!address || !out || capacity < 6U) { return false; }
    out[0] = address; out[1] = 0xF3U; out[2] = 0xABU;
    out[3] = enable ? 1U : 0U; out[4] = 0U; out[5] = 0x6BU;
    return true;
}
bool EMM_V5_BuildStop(uint8_t address, uint8_t *out, uint16_t capacity)
{
    if (!address || !out || capacity < 5U) { return false; }
    out[0] = address; out[1] = 0xFEU; out[2] = 0x98U; out[3] = 0U; out[4] = 0x6BU;
    return true;
}
uint8_t EMM_V5_ReplyLength(uint8_t function)
{
    switch (function) {
    case 0x1F: return 7U;
    case 0x21: return 15U; /* Emm PID; X firmware has a different layout. */
    case 0x23: return 7U;
    case 0x42: return 33U; /* Emm driver configuration. */
    case 0x1A: case 0x24: case 0x27: return 5U;
    case 0x33: case 0x34: case 0x36: case 0x37: return 8U;
    case 0x3A: case 0x3B: case 0xF3: case 0xFD: case 0xFE: return 4U;
    default: return 0U;
    }
}
bool EMM_V5_BuildRead(uint8_t address, uint8_t function, uint8_t *out, uint16_t capacity)
{
    if (!address || !out || capacity < 3U || function >= 0xF0U ||
        EMM_V5_ReplyLength(function) == 0U) { return false; }
    if (function == 0x42U) {
        if (capacity < 4U) { return false; }
        out[0] = address; out[1] = function; out[2] = 0x6CU; out[3] = 0x6BU;
        return true;
    }
    out[0] = address; out[1] = function; out[2] = 0x6BU;
    return true;
}
bool EMM_V5_IsAckStatus(uint8_t value)
{
    return value == 0x02U || value == 0x9FU || value == 0xE2U ||
           value == 0xEEU || value == 0x12U || value == 0x22U;
}
