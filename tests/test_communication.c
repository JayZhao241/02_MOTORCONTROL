#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "stm32f4xx_hal.h"
static uint32_t tick, irq_mask;
static int direction;
static uint8_t tx[128];
static uint16_t tx_len, dma_pos;
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
{ (void)h; memcpy(tx, b, n); tx_len = n; return tx_hal; }
HAL_StatusTypeDef HAL_UART_AbortTransmit_IT(UART_HandleTypeDef *h)
{ (void)h; return HAL_OK; }
/* Include production units to inject DMA bytes and inspect private state.
 * No protocol or queue algorithms are reimplemented in the test. */
#include "../Core/BSP/rs485.c"
#include "../Core/BSP/zdt_x42s.c"
static void inject(const uint8_t *b, unsigned n)
{
    for (unsigned i=0; i<n; ++i) {
        s_dma_rx_buffer[dma_pos] = b[i];
        dma_pos = (dma_pos + 1U) % 128U;
        dma.counter = dma_pos ? 128U - dma_pos : 128U;
        if (dma_pos == 64U || dma_pos == 0U) {
            HAL_UARTEx_RxEventCallback(&huart2, dma_pos ? dma_pos : 128U);
        }
    }
    HAL_UARTEx_RxEventCallback(&huart2, dma_pos ? dma_pos : 128U);
}
static void reply(const uint8_t *b, unsigned n)
{ HAL_UART_TxCpltCallback(&huart2); inject(b,n); ZDT_X42S_Proc(); }
static void prepare(void)
{
    const uint8_t options[]={1,0x1A,6,0x6B}, status[]={1,0x3A,3,0x6B};
    const uint8_t pos[]={1,0x36,0,0,0,0,0,0x6B};
    assert(ZDT_X42S_StartOptionsQuery(1)==RS485_OK); reply(options,sizeof options);
    assert(ZDT_X42S_StartStatusQuery(1)==RS485_OK); reply(status,sizeof status);
    assert(ZDT_X42S_StartPositionQuery(1)==RS485_OK); reply(pos,sizeof pos);
}
int main(void)
{
    assert(RS485_Init()==RS485_OK && direction==0);
    uint16_t count=99;
    assert(RS485_Read(NULL,1,&count)==RS485_INVALID_PARAM && count==0);
    /* DMA half/full/wrap, duplicate and stale Size callbacks, queue clear scope. */
    uint8_t data[200], out[256];
    for (unsigned i=0;i<sizeof data;i++) {data[i]=(uint8_t)i;}
    inject(data,200);
    HAL_UARTEx_RxEventCallback(&huart2,64); /* stale HT Size, live cursor unchanged */
    assert(RS485_Read(out,256,&count)==RS485_OK && count==200);
    assert(!memcmp(data,out,200));
    uint16_t cursor=s_dma_last_pos;
    assert(RS485_ClearRx()==RS485_OK && cursor==s_dma_last_pos);
    inject(data,200); inject(data,100);
    assert(RS485_Read(out,256,&count)==RS485_RX_OVERFLOW && count==0);
    assert(RS485_ClearRx()==RS485_OK);
    assert(ZDT_X42S_StartVersionQuery(0)==RS485_INVALID_PARAM);
    assert(ZDT_X42S_StartVersionQuery(1)==RS485_OK && direction==1);
    assert(RS485_ClearRx()==RS485_BUSY);
    assert(ZDT_X42S_StartStatusQuery(1)==RS485_BUSY);
    const uint8_t bad[]={0,0xFF,1,0x1F,0x99,0,0};
    inject(bad,sizeof bad); ZDT_X42S_Proc();
    const uint8_t version[]={1,0x1F,0,0xCE,0x23,0x14,0x6B};
    reply(version,3); assert(s_query.transaction==ZDT_TXN_WAIT);
    assert(s_query.state==ZDT_VERSION_WAIT_RESPONSE && s_query.rx_len<=8);
    inject(version+3,4); ZDT_X42S_Proc();
    assert(s_query.transaction==ZDT_TXN_SUCCESS && s_query.version.fw_version==206);
    assert(s_query.state==ZDT_VERSION_SUCCESS && s_query.rx_len==7);
    assert(!ZDT_X42S_ParseVersion(version,6,1,&s_query.version));
    const uint8_t status02[]={1,0x3A,2,0x6B};
    assert(ZDT_X42S_StartStatusQuery(1)==RS485_OK); reply(status02,4);
    assert(s_query.status.arrived && !s_query.status.enabled && !s_query.reply_status_valid);
    /* Wrong address cannot complete transaction; timeout blocks retransmission. */
    assert(ZDT_X42S_StartStatusQuery(1)==RS485_OK);
    const uint8_t wrong[]={2,0x3A,3,0x6B}; reply(wrong,4);
    assert(s_query.transaction==ZDT_TXN_WAIT);
    tick+=100; ZDT_X42S_Proc();
    assert(s_query.resync_required && s_query.transaction==ZDT_TXN_TIMEOUT);
    reply(status02,4); assert(s_query.transaction==ZDT_TXN_TIMEOUT);
    assert(ZDT_X42S_StartStatusQuery(1)==RS485_NOT_READY);
    assert(ZDT_X42S_ResumeAfterTimeout()==RS485_OK);
    prepare();
    assert(ZDT_X42S_TestRelative(1,0,0,5)==RS485_INVALID_PARAM);
    assert(ZDT_X42S_TestRelative(1,2,10,5)==RS485_INVALID_PARAM);
    assert(ZDT_X42S_TestRelative(1,0,10,11)==RS485_INVALID_PARAM);
    assert(ZDT_X42S_TestRelative(1,0,80,5)==RS485_OK);
    const uint8_t expected[]={1,0xFD,0,0,5,10,0,0,0,80,2,0,0x6B};
    assert(tx_len==13 && !memcmp(tx,expected,13));
    const uint8_t ack[]={1,0xFD,2,0x6B}; reply(ack,4);
    assert(s_query.command_accepted && !s_query.arrived && !s_query.started_observed);
    assert(ZDT_X42S_TestRelative(1,0,10,5)==RS485_BUSY);
    const uint8_t not_arrived[]={1,0x3A,1,0x6B};
    assert(ZDT_X42S_StartStatusQuery(1)==RS485_OK); reply(not_arrived,4);
    assert(s_query.saw_not_arrived && !s_query.started_observed);
    const uint8_t pos[]={1,0x36,0,0,0,1,0,0x6B};
    assert(ZDT_X42S_StartPositionQuery(1)==RS485_OK); reply(pos,8);
    assert(s_query.started_observed);
    const uint8_t arrived[]={1,0x3A,3,0x6B};
    assert(ZDT_X42S_StartStatusQuery(1)==RS485_OK); reply(arrived,4);
    assert(s_query.arrived && s_query.motion==ZDT_MOTION_ARRIVED);
    /* Async 9F must not finish an unrelated query. */
    prepare(); assert(ZDT_X42S_TestRelative(1,1,10,5)==RS485_OK); reply(ack,4);
    assert(ZDT_X42S_StartStatusQuery(1)==RS485_OK);
    const uint8_t done[]={1,0xFD,0x9F,0x6B}; reply(done,4);
    assert(s_query.arrived && s_query.transaction==ZDT_TXN_WAIT); reply(arrived,4);
    const uint8_t errors[]={0xE2,0xEE,0x12,0x22};
    for (unsigned i=0;i<sizeof errors;i++) {
        prepare(); assert(ZDT_X42S_TestRelative(1,0,10,5)==RS485_OK);
        uint8_t rejected[]={1,0xFD,errors[i],0x6B}; reply(rejected,4);
        assert(s_query.transaction==ZDT_TXN_REJECTED && s_query.motion==ZDT_MOTION_REJECTED);
    }
    prepare(); s_query.options=4; /* X firmware never emits Emm motion. */
    assert(ZDT_X42S_TestRelative(1,0,10,5)==RS485_NOT_READY);
    s_query.options=0x86; assert(ZDT_X42S_TestRelative(1,0,10,5)==RS485_NOT_READY);
    /* Full-width pulse encoding and parameter checks before narrowing. */
    prepare();
    ZDT_EmmMove_t m={1,1,3000,255,UINT32_MAX,1,120000};
    m.speed_rpm=65536; assert(ZDT_X42S_StartEmmMove(&m)==RS485_INVALID_PARAM);
    m.speed_rpm=3000; m.acceleration=256;
    assert(ZDT_X42S_StartEmmMove(&m)==RS485_INVALID_PARAM);
    m.acceleration=255; assert(ZDT_X42S_StartEmmMove(&m)==RS485_OK);
    assert(tx[3]==0x0B && tx[4]==0xB8 && tx[5]==255 && tx[6]==255 && tx[9]==255);
    const uint8_t reject[]={1,0xFD,0xE2,0x6B}; reply(reject,4);
    /* Motion deadline is not reported as arrival. */
    prepare(); assert(ZDT_X42S_TestRelative(1,0,10,5)==RS485_OK); reply(ack,4);
    tick+=10000; ZDT_X42S_Proc();
    assert(s_query.motion==ZDT_MOTION_TIMEOUT && s_query.resync_required && !s_query.arrived);
    assert(ZDT_X42S_ResumeAfterTimeout()==RS485_OK);
    /* Response timeout across the uint32 tick wrap. */
    tick=UINT32_MAX-49U; assert(ZDT_X42S_StartVersionQuery(1)==RS485_OK);
    HAL_UART_TxCpltCallback(&huart2);
    tick=49U; ZDT_X42S_Proc(); assert(s_query.transaction==ZDT_TXN_WAIT);
    tick=50U; ZDT_X42S_Proc(); assert(s_query.transaction==ZDT_TXN_TIMEOUT);
    assert(ZDT_X42S_ResumeAfterTimeout()==RS485_OK);
    tx_hal=HAL_BUSY; assert(RS485_SendAsync(data,3)==RS485_BUSY);
    assert(s_debug.state==RS485_STATE_READY && direction==0); tx_hal=HAL_OK;
    assert(RS485_SendAsync(data,3)==RS485_OK);
    tick+=30U; RS485_Proc();
    assert(s_debug.state==RS485_STATE_FAULT && s_debug.last_tx_result==RS485_TIMEOUT);
    HAL_UART_TxCpltCallback(&huart2); assert(s_debug.state==RS485_STATE_FAULT);
    /* Test-only reset of fault latch, to independently exercise UART error. */
    s_debug.state=RS485_STATE_READY;
    /* UART error remains latched despite a late transmit completion. */
    assert(RS485_SendAsync(data,3)==RS485_OK);
    huart2.ErrorCode=8; HAL_UART_ErrorCallback(&huart2);
    HAL_UART_TxCpltCallback(&huart2); RS485_Proc();
    assert(s_debug.state==RS485_STATE_FAULT && s_debug.last_hal_error==8 && direction==0);
    puts("communication tests passed");
}
