#ifndef BSP_KEYCONTROL_H
#define BSP_KEYCONTROL_H

#include <stdint.h>
#define KEY_EVENT_K0 (1U << 0)
#define KEY_EVENT_K1 (1U << 1)
#define KEY_EVENT_K2 (1U << 2)

void KEY_Time(void);
/* Legacy blocking STEP/DIR handler: retained, not scheduled by main. */
void KEY_Proc(void);
/* Main-loop only: atomically consume and clear sampled release events. */
uint8_t KEY_TakePress(void);

#endif /* BSP_KEYCONTROL_H */
