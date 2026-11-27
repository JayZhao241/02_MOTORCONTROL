#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "stm32f4xx_hal.h"
static uint32_t tick, irq_mask;
static int direction;
static uint8_t tx[128];
static uint16_t tx_len, dma_pos;
static unsigned transmissions;
static HAL_StatusTypeDef tx_hal = HAL_OK;
static DMA_HandleTypeDef dma;
UART_HandleTypeDef huart2 = {&dma, 0};
uint32_t HAL_GetTick(void) { return tick; }
uint32_t __get_PRIMASK(void) { return irq_mask; }
void __disable_irq(void) { irq_mask = 1; }
void __set_PRIMASK(uint32_t mask) { irq_mask = mask; }
void HAL_GPIO_WritePin(int port, int pin, int level)
{ assert(port == 7 && pin == 8); direction = level; }
HAL_StatusTypeDef HAL_UARTEx_ReceiveToIdle_DMA(UART_HandleTypeDef *h, uint8_t *b, uint16_t n)
{ (void)h; (void)b; assert(n == 128); dma.counter = n; return HAL_OK; }
HAL_StatusTypeDef HAL_UART_Transmit_DMA(UART_HandleTypeDef *h, uint8_t *b, uint16_t n)
{ (void)h; memcpy(tx, b, n); tx_len = n; transmissions++; return tx_hal; }
HAL_StatusTypeDef HAL_UART_AbortTransmit_IT(UART_HandleTypeDef *h)
{ (void)h; return HAL_OK; }
/* Real driver included only to inject DMA bytes; protocol/application are
 * separately compiled production units, with all assertions through public APIs. */
#include "../Core/BSP/rs485.c"
#include "emm_v5.h"
#include "zdt_x42s.h"
#include "motor_app.h"
static void inject(const uint8_t *b, unsigned n)
{
    for (unsigned i = 0U; i < n; i++) {
        s_dma_rx_buffer[dma_pos] = b[i];
        dma_pos = (uint16_t)((dma_pos + 1U) % 128U);
        dma.counter = dma_pos ? 128U - dma_pos : 128U;
        if (dma_pos == 64U || dma_pos == 0U) {
            HAL_UARTEx_RxEventCallback(&huart2, dma_pos ? dma_pos : 128U);
        }
    }
    HAL_UARTEx_RxEventCallback(&huart2, dma_pos ? dma_pos : 128U);
}
static void pump(unsigned ms)
{
    while (ms--) { tick++; RS485_Proc(); ZDT_X42S_Proc(); MOTOR_AppProc(); }
}
static void reply(const uint8_t *data, unsigned n)
{ HAL_UART_TxCpltCallback(&huart2); inject(data,n); pump(1); }
static ZDT_Debug_t protocol(void)
{ ZDT_Debug_t value; assert(ZDT_X42S_GetDebug(&value)); return value; }
static MOTOR_AppDebug_t application(void)
{ MOTOR_AppDebug_t value; assert(MOTOR_AppGetDebug(&value)); return value; }
static const uint8_t options[] = {1,0x1A,0,6,0x6B};
static const uint8_t enabled[] = {1,0x3A,1,0x6B};
static const uint8_t arrived[] = {1,0x3A,3,0x6B};
static const uint8_t position0[] = {1,0x36,0,0,0,0,0,0x6B};
static const uint8_t enable_ack[] = {1,0xF3,2,0x6B};
static const uint8_t move_ack[] = {1,0xFD,2,0x6B};
static const ZDT_EmmMove_t jog = {1,0,5,0,80,0,10000};
static void start_move(const ZDT_EmmMove_t *move, const uint8_t status[4])
{
    assert(MOTOR_AppMove(move) == RS485_OK);
    pump(10); assert(tx[1] == 0x1A && application().waiting_for_reply);
    reply(options,sizeof(options));
    assert(application().stage == MOTOR_APP_ENABLE);
    pump(9); assert(tx[1] == 0x1A); /* 10ms inter-frame gap. */
    pump(1); assert(tx[1] == 0xF3);
    const uint8_t expected_enable[] = {1,0xF3,0xAB,1,0,0x6B};
    assert(tx_len == 6 && !memcmp(tx,expected_enable,6));
    reply(enable_ack,sizeof(enable_ack));
    pump(10); assert(tx[1] == 0x3A);
    reply(status,4);
    pump(10); assert(tx[1] == 0x36);
    reply(position0,sizeof(position0));
    pump(10); assert(tx[1] == 0xFD && tx_len == 13);
    const uint8_t expected_move[] = {1,0xFD,0,0,5,0,0,0,0,(uint8_t)move->pulses,0,0,0x6B};
    assert(!memcmp(tx,expected_move,13));
}
static void start_jog(void) { start_move(&jog, enabled); }
int main(void)
{
    uint8_t frame[13];
    /* Golden vendor Emm_V5_Pos_Control(1,0,1000,0,32000,0,0). */
    ZDT_EmmMove_t example = {1,0,1000,0,32000,0,10000};
    const uint8_t golden[] = {1,0xFD,0,3,0xE8,0,0,0,0x7D,0,0,0,0x6B};
    assert(EMM_V5_BuildPosition(&example,frame,sizeof(frame)));
    assert(!memcmp(frame,golden,13));
    example.pulses = UINT32_MAX; example.acceleration = 255; example.direction = 1;
    assert(EMM_V5_BuildPosition(&example,frame,sizeof(frame)) && frame[6] == 255 && frame[9] == 255);
    example.speed_rpm = 65536U; assert(!EMM_V5_BuildPosition(&example,frame,sizeof(frame)));
    assert(!EMM_V5_BuildPosition(NULL,frame,sizeof(frame)));
    assert(!EMM_V5_BuildRead(0,0x3A,frame,sizeof(frame)));
    assert(!EMM_V5_BuildRead(1,0xFD,frame,sizeof(frame)));
    assert(!EMM_V5_BuildRead(1,0x99,frame,sizeof(frame)));
    assert(!EMM_V5_BuildRead(1,0x42,frame,3));
    assert(EMM_V5_BuildRead(1,0x42,frame,sizeof(frame)));
    const uint8_t config_request[]={1,0x42,0x6C,0x6B};
    assert(!memcmp(frame,config_request,4));
    assert(RS485_Init() == RS485_OK && direction == 0);
    MOTOR_AppInit();
    assert(MOTOR_AppMove(&jog) == RS485_NOT_READY);
    assert(MOTOR_AppRecover() == RS485_NOT_READY);
    pump(499); assert(application().stage == MOTOR_APP_BOOT_WAIT && transmissions == 0);
    pump(1); assert(application().stage == MOTOR_APP_IDLE && transmissions == 0);

    /* DMA wrap, stale event Size, duplicate cursor, queue overflow and clear. */
    uint8_t bytes[200], out[256]; uint16_t count;
    for (unsigned i=0;i<200;i++) { bytes[i]=(uint8_t)i; }
    inject(bytes,200); HAL_UARTEx_RxEventCallback(&huart2,64);
    assert(RS485_Read(out,256,&count)==RS485_OK && count==200 && !memcmp(out,bytes,200));
    uint16_t cursor = s_dma_last_pos;
    assert(RS485_ClearRx()==RS485_OK && cursor==s_dma_last_pos);
    inject(bytes,200); inject(bytes,100);
    assert(RS485_Read(out,256,&count)==RS485_RX_OVERFLOW && count==0);
    assert(RS485_ClearRx()==RS485_OK);

    /* Fragmented/noisy version stream, wrong address and timeout quarantine. */
    assert(MOTOR_AppQuery(1,0x1F)==RS485_OK); pump(1);
    const uint8_t noise[]={0,0xFF,1,0x1F,0x99,0,0}; inject(noise,sizeof(noise));
    const uint8_t version[]={1,0x1F,0,0xCE,0x23,0x14,0x6B};
    reply(version,3); assert(protocol().transaction==ZDT_TXN_WAIT);
    inject(version+3,4); pump(1);
    assert(protocol().version_valid && protocol().version.fw_version==206);
    assert(application().stage==MOTOR_APP_DONE);
    assert(MOTOR_AppQuery(1,0x3A)==RS485_OK); pump(10);
    const uint8_t wrong[]={2,0x3A,3,0x6B}; reply(wrong,4);
    assert(protocol().transaction==ZDT_TXN_WAIT);
    pump(100); assert(protocol().resync_required && application().stage==MOTOR_APP_FAILED);
    reply(arrived,4); assert(protocol().transaction==ZDT_TXN_TIMEOUT);
    assert(MOTOR_AppQuery(1,0x3A)==RS485_NOT_READY);
    assert(MOTOR_AppRecover()==RS485_OK);

    /* Actual five-byte 1A reply, high option bits and embedded 6B payload. */
    assert(MOTOR_AppQuery(1,0x1A)==RS485_OK); pump(10);
    const uint8_t opt_tail[]={1,0x1A,1,0x6B,0x6B};
    reply(opt_tail,4); assert(protocol().transaction==ZDT_TXN_WAIT);
    inject(opt_tail+4,1); pump(1); assert(protocol().options==0x016B && protocol().last_frame_len==5);
    assert(MOTOR_AppRecover()==RS485_OK);

    /* Complete app path and non-destructive repeated-key BUSY result. */
    start_jog(); reply(move_ack,4);
    assert(application().stage==MOTOR_APP_MONITOR && protocol().command_accepted);
    ZDT_EmmMove_t other=jog; other.direction=1; other.pulses=10;
    assert(MOTOR_AppMove(&other)==RS485_BUSY);
    assert(application().result==RS485_OK && application().last_request_result==RS485_BUSY);
    assert(application().move.direction==0 && application().move.pulses==80);
    pump(50); assert(tx[1]==0x3A); reply(enabled,4);
    assert(protocol().motion==ZDT_MOTION_ACCEPTED && !protocol().started_observed);
    pump(50); assert(tx[1]==0x36);
    const uint8_t pos[]={1,0x36,0,0,0,2,0,0x6B}; reply(pos,8);
    assert(protocol().started_observed && protocol().position_delta==512);
    pump(50); assert(tx[1]==0x33);
    const uint8_t target[]={1,0x33,0,0,0,2,0,0x6B}; reply(target,8);
    pump(50); assert(tx[1]==0x24);
    const uint8_t voltage[]={1,0x24,0x2E,0xE0,0x6B}; reply(voltage,5);
    pump(50); assert(tx[1]==0x27);
    const uint8_t current[]={1,0x27,1,0xF4,0x6B}; reply(current,5);
    pump(50); assert(tx[1]==0x34);
    const uint8_t setpoint[]={1,0x34,0,0,0,2,0,0x6B}; reply(setpoint,8);
    assert(protocol().setpoint_valid && protocol().setpoint_position==512);
    pump(50); assert(tx[1]==0x3A); reply(arrived,4);
    assert(application().stage==MOTOR_APP_DONE && protocol().arrived);
    assert(protocol().bus_voltage_mv==12000 && protocol().phase_current_ma==500);
    assert(protocol().move_reply_valid && !memcmp(protocol().move_reply,move_ack,4));

    /* Receive mode sends only 02. A one-pulse move can keep Prf_TF set
     * throughout. Require the trajectory to reach the final target first. */
    ZDT_EmmMove_t single = jog;
    single.pulses = 1U;
    start_move(&single, arrived); reply(move_ack,4);
    const uint8_t trajectory_positions[] = {0U, 20U};
    for (unsigned i = 0U; i < sizeof(trajectory_positions); i++) {
        pump(50); assert(tx[1]==0x3A); reply(arrived,4);
        assert(application().stage==MOTOR_APP_MONITOR && !protocol().saw_not_arrived);
        assert(MOTOR_AppMove(&single)==RS485_BUSY);
        const uint8_t single_position[]={1,0x36,0,0,0,0,20,0x6B};
        const uint8_t single_target[]={1,0x33,0,0,0,0,20,0x6B};
        const uint8_t trajectory[]={1,0x34,0,0,0,0,trajectory_positions[i],0x6B};
        pump(50); assert(tx[1]==0x36); reply(single_position,8);
        pump(50); assert(tx[1]==0x33); reply(single_target,8);
        pump(50); assert(tx[1]==0x24); reply(voltage,5);
        pump(50); assert(tx[1]==0x27); reply(current,5);
        pump(50); assert(tx[1]==0x34); reply(trajectory,8);
    }
    assert(application().stage==MOTOR_APP_MONITOR);
    pump(50); assert(tx[1]==0x3A); reply(arrived,4);
    assert(application().stage==MOTOR_APP_DONE && protocol().arrived);
    assert(!protocol().saw_not_arrived && !protocol().resync_required);
    start_move(&single, arrived); reply(move_ack,4);
    assert(!protocol().target_valid && !protocol().setpoint_valid);
    const uint8_t stalled_at_target[]={1,0x3A,0x0B,0x6B};
    pump(50); assert(tx[1]==0x3A); reply(stalled_at_target,4);
    assert(application().error==MOTOR_APP_STALL && !protocol().arrived);
    assert(MOTOR_AppRecover()==RS485_OK);

    /* Bad options: no enable or FD emitted; motor rejection: no motion retry. */
    assert(MOTOR_AppMove(&jog)==RS485_OK); pump(10);
    const uint8_t x_options[]={1,0x1A,0,4,0x6B}; reply(x_options,5);
    assert(application().error==MOTOR_APP_BAD_OPTIONS && tx[1]==0x1A);
    assert(MOTOR_AppRecover()==RS485_OK);
    assert(MOTOR_AppMove(&jog)==RS485_OK); pump(10); reply(options,5); pump(10);
    const uint8_t enable_bad[]={1,0xF3,0xE2,0x6B}; reply(enable_bad,4);
    assert(application().stage==MOTOR_APP_FAILED && application().failed_stage==MOTOR_APP_ENABLE);
    assert(application().error==MOTOR_APP_REPLY_REJECTED && tx[1]==0xF3);
    assert(MOTOR_AppRecover()==RS485_OK);

    /* Priority stop while a status transaction is waiting. */
    assert(MOTOR_AppMove(&jog)==RS485_OK); pump(10); reply(options,5); pump(10);
    reply(enable_ack,4); pump(10); assert(tx[1]==0x3A);
    assert(MOTOR_AppStop(1)==RS485_OK); pump(1);
    assert(tx[1]==0x3A); /* UART TX still busy: do not overwrite DMA buffer. */
    HAL_UART_TxCpltCallback(&huart2); pump(1);
    const uint8_t expected_stop[]={1,0xFE,0x98,0,0x6B};
    assert(tx_len==5 && !memcmp(tx,expected_stop,5));
    inject(arrived,4); pump(1); assert(protocol().transaction==ZDT_TXN_WAIT);
    const uint8_t stop_ack[]={1,0xFE,2,0x6B}; reply(stop_ack,4);
    assert(application().stage==MOTOR_APP_STOP_ACCEPTED && protocol().cancelled_transactions==1);
    assert(protocol().resync_required && MOTOR_AppMove(&jog)==RS485_NOT_READY);
    assert(MOTOR_AppRecover()==RS485_OK);
    unsigned sent=transmissions; pump(500); assert(transmissions==sent);

    /* First 1A succeeds, following 3A missing: fail without ever sending FD. */
    assert(MOTOR_AppMove(&jog)==RS485_OK); pump(10); reply(options,5); pump(10);
    reply(enable_ack,4); pump(10); assert(tx[1]==0x3A);
    HAL_UART_TxCpltCallback(&huart2); pump(100);
    assert(application().stage==MOTOR_APP_FAILED && application().failed_stage==MOTOR_APP_STATUS);
    assert(!protocol().command_accepted && protocol().function==0x3A);
    assert(MOTOR_AppRecover()==RS485_OK);

    /* 9F completes a small move even if polling misses its intermediate state. */
    start_jog(); const uint8_t done[]={1,0xFD,0x9F,0x6B}; reply(done,4);
    assert(application().stage==MOTOR_APP_DONE && protocol().arrived);
    assert(!protocol().status_valid && !protocol().position_valid);
    assert(ZDT_X42S_StartEmmMove(&jog)==RS485_NOT_READY);
    assert(MOTOR_AppRecover()==RS485_OK);
    start_jog(); reply(move_ack,4);
    /* Simulate a long scheduler stall; no second motor command is attempted. */
    tick+=10000; RS485_Proc(); ZDT_X42S_Proc(); MOTOR_AppProc();
    assert(protocol().motion==ZDT_MOTION_TIMEOUT && application().error==MOTOR_APP_NO_ARRIVAL);
    assert(MOTOR_AppRecover()==RS485_OK);

    /* A position payload can contain an entire FD completion-shaped sequence.
     * Keep the outer frame intact, including when its final byte arrives later. */
    start_jog(); reply(move_ack,4);
    pump(50); reply(enabled,4);
    pump(50); assert(tx[1]==0x36);
    const uint8_t embedded_done[]={1,0x36,0,1,0xFD,0x9F,0x6B,0x6B};
    reply(embedded_done,7);
    assert(protocol().transaction==ZDT_TXN_WAIT && !protocol().arrived);
    assert(application().stage==MOTOR_APP_MONITOR);
    inject(embedded_done+7,1); pump(1);
    assert(protocol().position_valid && protocol().position==0x01FD9F6B);
    assert(protocol().motion==ZDT_MOTION_MOVING && !protocol().arrived);
    reply(done,4); assert(application().stage==MOTOR_APP_DONE);

    /* Stop on a different address must not relabel the previous motor's cache. */
    assert(MOTOR_AppStop(2)==RS485_OK); pump(10);
    const uint8_t stop2[]={2,0xFE,2,0x6B}; reply(stop2,4);
    assert(!protocol().options_valid && !protocol().position_valid && !protocol().status_valid);
    ZDT_EmmMove_t motor2=jog; motor2.address=2;
    assert(ZDT_X42S_StartEmmMove(&motor2)==RS485_NOT_READY);
    assert(MOTOR_AppRecover()==RS485_OK);

    /* A valid but slow FD ACK must reach the application before polling starts. */
    start_jog(); HAL_UART_TxCpltCallback(&huart2); pump(60);
    reply(move_ack,4);
    assert(application().stage==MOTOR_APP_MONITOR && protocol().transaction==ZDT_TXN_SUCCESS);
    assert(tx[1]==0xFD);
    pump(49); assert(tx[1]==0xFD);
    pump(1); assert(tx[1]==0x3A); reply(enabled,4);
    reply(done,4); assert(application().stage==MOTOR_APP_DONE);

    /* Changing enable state invalidates the earlier status snapshot immediately. */
    assert(MOTOR_AppEnable(1,false)==RS485_OK); pump(10);
    assert(tx[1]==0xF3 && !protocol().status_valid);
    reply(enable_ack,4); assert(!protocol().status_valid);
    assert(ZDT_X42S_StartEmmMove(&jog)==RS485_NOT_READY);
    assert(MOTOR_AppRecover()==RS485_OK);

    /* Cancelling a version read must leave its legacy state terminal too. */
    assert(MOTOR_AppQuery(1,0x1F)==RS485_OK); pump(10);
    HAL_UART_TxCpltCallback(&huart2);
    assert(MOTOR_AppStop(1)==RS485_OK); pump(10); reply(stop_ack,4);
    assert(protocol().state!=ZDT_VERSION_WAIT_RESPONSE && !protocol().version_valid);
    assert(MOTOR_AppRecover()==RS485_OK);

    /* Explicit diagnostics never send a motion, calibration or parameter write.
     * PID/config reply formats are enabled only after same-address Emm options. */
    assert(ZDT_X42S_StartQuery(1,0x21)==RS485_NOT_READY);
    assert(ZDT_X42S_StartQuery(1,0x42)==RS485_NOT_READY);
    assert(MOTOR_AppQuery(1,0x1A)==RS485_OK); pump(10); reply(options,5);
    assert(MOTOR_AppQuery(1,0x3B)==RS485_OK); pump(10);
    const uint8_t healthy[]={1,0x3B,3,0x6B}; reply(healthy,4);
    assert(protocol().health.valid && protocol().health.encoder_ready && protocol().health.calibrated);
    assert(!protocol().health.over_current && !protocol().health.over_temperature && !protocol().health.homing);
    assert(MOTOR_AppQuery(1,0x3B)==RS485_OK); pump(10);
    assert(!protocol().health.valid);
    const uint8_t unhealthy[]={1,0x3B,0x3C,0x6B}; reply(unhealthy,4);
    assert(!protocol().health.encoder_ready && !protocol().health.calibrated);
    assert(protocol().health.homing && protocol().health.homing_failed);
    assert(protocol().health.over_current && protocol().health.over_temperature);

    assert(MOTOR_AppQuery(1,0x21)==RS485_OK); pump(10);
    const uint8_t pid_default[]={1,0x21,0,0,0x46,0x50,0,0,0,10,0,0,0x46,0x50,0x6B};
    reply(pid_default,10); assert(protocol().transaction==ZDT_TXN_WAIT && !protocol().pid.valid);
    inject(pid_default+10,5); pump(1);
    assert(protocol().pid.valid && protocol().pid.kp==18000 && protocol().pid.ki==10 && protocol().pid.kd==18000);
    assert(MOTOR_AppQuery(1,0x23)==RS485_OK); pump(10);
    const uint8_t integral[]={1,0x23,0,0,0xFF,0xFF,0x6B}; reply(integral,7);
    assert(protocol().integral_limit_valid && protocol().integral_limit==65535);

    assert(MOTOR_AppQuery(1,0x42)==RS485_OK); pump(10);
    assert(tx_len==4 && !memcmp(tx,config_request,4));
    /* 33-byte example from the manual, with an embedded ACK-shaped payload.
     * Simulates the production 32-byte read limit as well as UART splitting. */
    uint8_t config[]={1,0x42,0x21,0x15,0x19,2,2,2,0,0x10,1,0,
        4,0xB0,0x0B,0x80,0x0F,0xA0,5,7,1,0,1,1,0,8,8,0x98,7,0xD0,0,8,0x6B};
    reply(config,32); assert(!protocol().config.valid && protocol().transaction==ZDT_TXN_WAIT);
    inject(config+32,1); pump(1);
    ZDT_EmmConfig_t cfg=protocol().config;
    assert(cfg.valid && cfg.microsteps==16 && cfg.open_loop_current_ma==1200);
    assert(cfg.max_current_ma==2944 && cfg.max_output_raw==4000); /* Manual example is 0B80, not 0BB8. */
    assert(cfg.pulse_mode==2 && cfg.serial_mode==2 && cfg.enable_level==2);
    assert(cfg.response_mode==1 && cfg.arrival_window_tenths_deg==8);
    assert(protocol().last_frame_len==33 && !memcmp(cfg.raw,config,33));
    assert(MOTOR_AppQuery(1,0x42)==RS485_OK); pump(10);
    config[9]=0; config[12]=1; config[13]=0xFD; config[14]=0x9F; config[15]=0x6B;
    reply(config,33); pump(1);
    assert(protocol().config.valid && protocol().config.microsteps==256);
    assert(protocol().config.open_loop_current_ma==509 && protocol().config.max_current_ma==40811);
    assert(protocol().motion==ZDT_MOTION_IDLE && !protocol().command_accepted);
    /* A configuration reply from X has a different header/length: ignore it. */
    assert(MOTOR_AppQuery(1,0x42)==RS485_OK); pump(10);
    uint8_t wrong_layout[37]={1,0x42,0x25,0x18}; wrong_layout[36]=0x6B;
    reply(wrong_layout,37); pump(1);
    assert(!protocol().config.valid && protocol().transaction==ZDT_TXN_WAIT);
    reply(config,33); pump(1); assert(protocol().config.valid);
    assert(MOTOR_AppQuery(2,0x3B)==RS485_OK); pump(10);
    const uint8_t healthy2[]={2,0x3B,3,0x6B}; reply(healthy2,4);
    assert(!protocol().pid.valid && !protocol().config.valid && !protocol().integral_limit_valid);
    assert(ZDT_X42S_StartQuery(1,0x42)==RS485_NOT_READY);
    assert(MOTOR_AppRecover()==RS485_OK);

    /* Trajectory position must not overwrite actual position, final target,
     * displacement or arrival evidence; validate its sign and fragments. */
    assert(MOTOR_AppQuery(1,0x34)==RS485_OK); pump(10);
    const uint8_t bad_setpoint[]={1,0x34,2,0,0,0,7,0x6B}; reply(bad_setpoint,8);
    assert(!protocol().setpoint_valid && protocol().transaction==ZDT_TXN_WAIT);
    const uint8_t negative_setpoint[]={1,0x34,1,0xFF,0xFF,0xFF,0xFF,0x6B};
    reply(negative_setpoint,4); assert(!protocol().setpoint_valid);
    inject(negative_setpoint+4,4); pump(1);
    assert(protocol().setpoint_valid && protocol().setpoint_position==-4294967295LL);
    assert(!protocol().position_valid && !protocol().target_valid && !protocol().started_observed);
    assert(MOTOR_AppQuery(2,0x3B)==RS485_OK); pump(10); reply(healthy2,4);
    assert(!protocol().setpoint_valid);
    assert(MOTOR_AppRecover()==RS485_OK);

    /* Watch angles use Emm encoder units, including negative position error. */
    assert(MOTOR_AppQuery(1,0x36)==RS485_OK); pump(10);
    const uint8_t fine_position[]={1,0x36,0,0,0,0,20,0x6B};
    reply(fine_position,8);
    assert(protocol().position_valid && protocol().position_deg==0.10986328125);
    assert(MOTOR_AppQuery(1,0x37)==RS485_OK); pump(10);
    const uint8_t error_query[]={1,0x37,0x6B};
    assert(tx_len==3 && !memcmp(tx,error_query,3));
    const uint8_t invalid_error[]={1,0x37,2,0,0,0,8,0x6B};
    reply(invalid_error,8);
    assert(!protocol().position_error_valid && protocol().transaction==ZDT_TXN_WAIT);
    const uint8_t negative_error[]={1,0x37,1,0,0,0,8,0x6B};
    inject(negative_error,4); pump(1); assert(!protocol().position_error_valid);
    inject(negative_error+4,4); pump(1);
    assert(protocol().position_error_valid && protocol().position_error==-8);
    assert(protocol().position_error_deg==-0.0439453125);
    assert(protocol().position_valid && protocol().position_deg==0.10986328125);
    assert(MOTOR_AppQuery(2,0x3B)==RS485_OK); pump(10); reply(healthy2,4);
    assert(!protocol().position_valid && !protocol().position_error_valid);
    assert(MOTOR_AppRecover()==RS485_OK);

    /* Persist the physical origin once, preserving unrelated homing limits. */
    assert(MOTOR_AppSaveStartupOrigin(1)==RS485_OK);
    pump(10); assert(tx[1]==0x1A); reply(options,5);
    pump(10); assert(tx[1]==0x22 && tx_len==3);
    uint8_t home_reply[]={1,0x22,1,1,0,30,0,0,0x27,0x10,1,0x2C,3,0x20,0,60,0,0x6B};
    reply(home_reply,sizeof(home_reply));
    pump(10); const uint8_t save_origin[]={1,0x93,0x88,1,0x6B};
    assert(tx_len==5 && !memcmp(tx,save_origin,5));
    const uint8_t origin_ack[]={1,0x93,2,0x6B}; reply(origin_ack,4);
    pump(249); assert(tx[1]==0x93);
    pump(1); const uint8_t clear_origin[]={1,0x0A,0x6D,0x6B};
    assert(tx_len==4 && !memcmp(tx,clear_origin,4));
    const uint8_t clear_ack[]={1,0x0A,2,0x6B}; reply(clear_ack,4);
    pump(10); assert(tx_len==20 && tx[1]==0x4C && tx[2]==0xAE && tx[3]==1);
    assert(tx[4]==0 && tx[5]==1 && tx[6]==0 && tx[7]==5 && tx[18]==1 && tx[19]==0x6B);
    assert(!memcmp(tx+8,home_reply+6,10));
    const uint8_t home_ack[]={1,0x4C,2,0x6B}; reply(home_ack,4);
    pump(249); assert(tx[1]==0x4C);
    pump(1); assert(tx[1]==0x22);
    home_reply[2]=0; home_reply[5]=5; home_reply[16]=1;
    reply(home_reply,sizeof(home_reply));
    assert(application().stage==MOTOR_APP_DONE && protocol().home.valid);
    assert(protocol().home.power_on==1 && protocol().home.speed_rpm==5);

    /* Response deadline handles tick wrap, fault latch survives late TC. */
    tick=UINT32_MAX-49U;
    assert(ZDT_X42S_StartVersionQuery(1)==RS485_OK); HAL_UART_TxCpltCallback(&huart2);
    tick=49; ZDT_X42S_Proc(); assert(protocol().transaction==ZDT_TXN_WAIT);
    tick=50; ZDT_X42S_Proc(); assert(protocol().transaction==ZDT_TXN_TIMEOUT);
    assert(ZDT_X42S_ResumeAfterTimeout()==RS485_OK);
    tx_hal=HAL_BUSY; assert(RS485_SendAsync(bytes,3)==RS485_BUSY && direction==0); tx_hal=HAL_OK;
    assert(RS485_SendAsync(bytes,3)==RS485_OK); tick+=30; RS485_Proc();
    assert(s_debug.state==RS485_STATE_FAULT && s_debug.last_tx_result==RS485_TIMEOUT);
    HAL_UART_TxCpltCallback(&huart2); assert(s_debug.state==RS485_STATE_FAULT);
    s_debug.state=RS485_STATE_READY; /* Test-only reset for an independent fault. */
    assert(RS485_SendAsync(bytes,3)==RS485_OK); huart2.ErrorCode=8;
    HAL_UART_ErrorCallback(&huart2); HAL_UART_TxCpltCallback(&huart2); RS485_Proc();
    assert(s_debug.state==RS485_STATE_FAULT && s_debug.last_hal_error==8 && direction==0);
    puts("vendor codec, RS485, protocol and motor application tests passed");
}
