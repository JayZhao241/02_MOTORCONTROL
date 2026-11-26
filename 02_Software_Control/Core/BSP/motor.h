#ifndef BSP_MOTOR_H
#define BSP_MOTOR_H

#include <stdint.h>

/* Blocking STEP/DIR control: finite angle in [0,360] degrees, dir in {0,1}.
 * Invalid inputs do not change GPIO. Call only from the main loop. */
void MOTOR_Control(float angle, uint8_t dir);

#endif /* BSP_MOTOR_H */
