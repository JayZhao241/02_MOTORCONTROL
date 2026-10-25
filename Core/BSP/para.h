#ifndef BSP_PARA_H
#define BSP_PARA_H

#include "stm32f4xx_hal.h"
#include <stdint.h>

typedef struct{
    uint8_t judge;
    uint8_t state;
    volatile uint8_t press;
}KEY_T;

extern KEY_T KEY[3];

#endif /* BSP_PARA_H */
