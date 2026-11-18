#include "keycontrol.h"
#include "para.h"
#include "motor.h"
#include <stdint.h>
#include "main.h"
#include <stdbool.h>

/* TIM6 samples every 10ms. Require two stable samples per edge.
 * A key held during power-up must first be released before it can trigger. */
void KEY_Time(void)
{
    static uint8_t release_count[3];
    static bool armed[3];
    KEY[0].state = HAL_GPIO_ReadPin(K0_GPIO_Port, K0_Pin);
    KEY[1].state = HAL_GPIO_ReadPin(K1_GPIO_Port, K1_Pin);
    KEY[2].state = HAL_GPIO_ReadPin(K2_GPIO_Port, K2_Pin);
    for (uint8_t i = 0U; i < 3U; i++) {
        if (KEY[i].state != 0U) {
            if (release_count[i] < 2U) { release_count[i]++; }
            if (KEY[i].judge == 1U) { KEY[i].judge = 0U; }
            if (release_count[i] == 2U) {
                if (armed[i] && KEY[i].judge == 2U) { KEY[i].press = 1U; }
                KEY[i].judge = 0U;
                armed[i] = true;
            }
        } else {
            release_count[i] = 0U;
            if (armed[i] && KEY[i].judge < 2U) { KEY[i].judge++; }
        }
    }
}

uint8_t KEY_TakePress(void)
{
    uint32_t mask = __get_PRIMASK();
    __disable_irq();
    uint8_t events = 0U;
    for (uint8_t i = 0U; i < 3U; i++) {
        if (KEY[i].press != 0U) { events |= (uint8_t)(1U << i); }
        KEY[i].press = 0U;
    }
    __set_PRIMASK(mask);
    return events;
}

void KEY_Proc(void)
{
    if (KEY[0].press==1) {
        MOTOR_Control(9, 0);
        KEY[0].press=0;
    }
    else if (KEY[2].press==1) {
        MOTOR_Control(9, 1);
        KEY[2].press=0;
    }
}
