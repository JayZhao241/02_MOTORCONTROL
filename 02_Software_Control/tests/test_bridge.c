#include <assert.h>
#include <stdio.h>
#include "motor_bridge.h"
static uint32_t tick;
static uint8_t addresses[32], count;
static RS485_Result_t send_result=RS485_OK;
static RS485_Debug_t driver={.state=RS485_STATE_READY};
uint32_t HAL_GetTick(void) { return tick; }
bool RS485_IsTxBusy(void) { return false; }
bool RS485_GetDebug(RS485_Debug_t *out) { *out=driver; return true; }
RS485_Result_t RS485_ClearRx(void) { return RS485_OK; }
RS485_Result_t RS485_SendAsync(const uint8_t *p,uint16_t n) { (void)n; addresses[count++]=p[0]; return send_result; }
RS485_Result_t RS485_Read(uint8_t *b,uint16_t cap,uint16_t *n) { (void)b; (void)cap; *n=0; return RS485_OK; }
static void Request(uint32_t request) { s_bridge.arm=0x4455414CU; s_bridge.request=request; }
static void FinishStop(void) { for (int i=0;i<4;i++) { MOTOR_BridgeProc(false,false); } }
int main(void) {
    assert(!MOTOR_BridgeProc(false,false)); assert(count==0);
    Request(1); assert(!MOTOR_BridgeProc(true,false)); assert(s_bridge.result==RS485_BUSY);
    Request(1); assert(MOTOR_BridgeProc(false,false)); assert(count==0);
    s_bridge.tx_len=3; s_bridge.tx[0]=3; s_bridge.tx[1]=0x36; s_bridge.tx[2]=0x6B;
    Request(2); MOTOR_BridgeProc(false,false); assert(s_bridge.result==RS485_INVALID_PARAM); assert(count==0);
    s_bridge.tx[0]=2; Request(2); MOTOR_BridgeProc(false,false); assert(count==1 && addresses[0]==2);
    tick=1501; MOTOR_BridgeProc(false,false); assert(s_bridge.result==RS485_OK);
    tick=10001; assert(MOTOR_BridgeProc(false,false)); FinishStop(); assert(addresses[1]==1 && addresses[2]==2 && !s_bridge.active);
    Request(1); MOTOR_BridgeProc(false,false); MOTOR_BridgeProc(false,true); FinishStop();
    assert(addresses[3]==1 && addresses[4]==2 && !s_bridge.active);
    Request(1); MOTOR_BridgeProc(false,false);
    driver.state=RS485_STATE_FAULT; driver.last_hal_error=4; driver.last_error=RS485_HW_ERROR;
    send_result=RS485_HW_ERROR; Request(3); MOTOR_BridgeProc(false,false); FinishStop();
    assert(!s_bridge.active && s_bridge.stop_failed_mask==3U && s_bridge.result==RS485_HW_ERROR);
    assert(addresses[5]==1 && addresses[6]==2 && s_bridge.hal_error==4);
    Request(1); MOTOR_BridgeProc(false,false); assert(s_bridge.result==RS485_HW_ERROR && !s_bridge.active);
    assert(count==7); /* Faulted acquire never transmits or resets the driver. */
    puts("bridge ownership, watchdog, bounded failed stops and diagnostics passed");
}
