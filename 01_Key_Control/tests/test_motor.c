#include <assert.h>
#include <math.h>
#include <stdio.h>
#include "main.h"
#include "motor.h"

static unsigned writes, pulses, step_level;
static int direction;
static uint32_t elapsed;
void HAL_Delay(uint32_t milliseconds) { elapsed += milliseconds; }
void HAL_GPIO_WritePin(int port, int pin, int level)
{
    writes++;
    assert(pin == MOTOR_STEP_Pin);
    if (port == MOTOR_DIR_GPIO_Port) { direction = level; }
    else {
        assert(port == MOTOR_STEP_GPIO_Port);
        assert((unsigned)level != step_level);
        step_level = (unsigned)level;
        if (level == GPIO_PIN_SET) { pulses++; }
    }
}
int main(void)
{
    MOTOR_Control(-9.0f,0);
    MOTOR_Control(361.0f,0);
    MOTOR_Control(NAN,0);
    MOTOR_Control(INFINITY,0);
    MOTOR_Control(-INFINITY,0);
    MOTOR_Control(9.0f,2);
    assert(writes == 0U && elapsed == 0U);
    MOTOR_Control(9.0f,0);
    assert(pulses == 80U && step_level == 0U && direction == GPIO_PIN_RESET);
    assert(elapsed == 321U);
    MOTOR_Control(360.0f,1);
    assert(pulses == 3280U && step_level == 0U && direction == GPIO_PIN_SET);
    MOTOR_Control(0.05625f,0); /* Half an input pulse rounds up. */
    assert(pulses == 3281U);
    puts("STEP/DIR input validation and pulse tests passed");
}
