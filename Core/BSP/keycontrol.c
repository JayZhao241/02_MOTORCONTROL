#include "keycontrol.h"
#include <stdint.h>
#include "main.h"
#include "stm32f407xx.h"
#include "stm32f4xx_hal_gpio.h"

void KEY_Time()
{
    KEY[0].state=HAL_GPIO_ReadPin(GPIOE, GPIO_PIN_4);
    KEY[1].state=HAL_GPIO_ReadPin(GPIOE, GPIO_PIN_3);
    KEY[2].state=HAL_GPIO_ReadPin(GPIOE, GPIO_PIN_2);

    for (uint8_t i=0; i<3; i++) {
        switch (KEY[i].judge) {
            case 0:
                if (KEY[i].state==0) {
                    KEY[i].judge=1;
                }
                break;
            case 1:
                if (KEY[i].state==0) {
                    KEY[i].judge=2;
                }
                break;
            case 2:
                if (KEY[i].state==1) {
                    KEY[i].press=1;
                    KEY[i].judge=0;
                }
                break;
            default:
                break;
        }
    }
}

void KEY_Proc()
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
