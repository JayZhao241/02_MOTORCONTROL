#include <assert.h>
#include <stdio.h>
#include "main.h"
static unsigned levels = 0xFFFFU;
static uint32_t irq_mask;
uint32_t __get_PRIMASK(void) { return irq_mask; }
void __disable_irq(void) { irq_mask = 1U; }
void __set_PRIMASK(uint32_t mask) { irq_mask = mask; }
int HAL_GPIO_ReadPin(int port, int pin)
{ assert(port == 4); return (levels & (unsigned)pin) != 0U; }
void MOTOR_Control(float angle, uint8_t direction)
{ (void)angle; (void)direction; assert(!"Legacy blocking motor handler must not run"); }
#include "../Core/BSP/para.c"
#include "../Core/BSP/keycontrol.c"
static void sample(unsigned n) { while (n--) { KEY_Time(); } }
int main(void)
{
    levels &= ~K1_Pin; sample(10); /* Held through boot. */
    assert(KEY_TakePress() == 0U);
    levels |= K1_Pin; sample(2); assert(KEY_TakePress() == 0U);
    levels &= ~K1_Pin; sample(1); levels |= K1_Pin; sample(2);
    assert(KEY_TakePress() == 0U); /* One-sample press bounce. */
    levels &= ~K1_Pin; sample(2); sample(100);
    assert(KEY_TakePress() == 0U); /* No hold-to-repeat. */
    levels |= K1_Pin; sample(1); levels &= ~K1_Pin; sample(1);
    assert(KEY_TakePress() == 0U); /* Release bounce. */
    levels |= K1_Pin; sample(2);
    irq_mask = 1U;
    assert(KEY_TakePress() == KEY_EVENT_K1 && irq_mask == 1U);
    assert(KEY_TakePress() == 0U); irq_mask = 0U;
    levels &= ~(K1_Pin | K2_Pin); sample(2);
    levels |= K1_Pin | K2_Pin; sample(2);
    assert(KEY_TakePress() == (KEY_EVENT_K1 | KEY_EVENT_K2));
    levels &= ~K2_Pin; sample(2); levels |= K2_Pin; sample(2);
    assert(KEY_TakePress() == KEY_EVENT_K2);
    assert(KEY_TakePress() == 0U && irq_mask == 0U);
    puts("key debounce tests passed");
}
