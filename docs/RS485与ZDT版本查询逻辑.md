# 单电机通信与实机调试

本次审查基线：GitHub main / 本地 HEAD `e1fc7cabdd7d3bc15ea496571bc5af705c988bd6`。
用户确认实际电机为 **Emm 固件**。实际细分、步距角、限位有效电平、机械负载与应答配置尚未确认。
协议依据厂商《ZDT_X42S 第二代闭环步进电机用户手册 V1.0.5_260527》：
[公开手册副本](https://github.com/xhfb/emm_stepper/blob/main/ZDT_X42S第二代闭环步进电机用户手册V1.0.5_260527.pdf)。
核对章节：4.1.2、5.3.12、5.5.2、5.5.13、5.5.15、5.6.4。
位置命令仅实现 Emm；未实现 X 命令、广播、同步运动、自动使能、上电运动或自动重试。

## 文件与职责

| 文件 | 本次修改 |
|---|---|
| Core/BSP/rs485.c、rs485.h | 统一方向引脚宏；循环 DMA 实时游标搬运；队列清理；错误与发送结果快照；非阻塞发送终止 |
| Core/BSP/zdt_x42s.c、zdt_x42s.h | 单事务、流解析、版本/状态/选项/位置查询、Emm 位置命令、运动证据、超时隔离 |
| Core/Src/main.c | 单个 Watch 调试结构体、显式一次性请求；关闭按键业务；取消上电自动查询与等待 |
| Core/Inc/main.h | 移除 BSP 聚合包含，打断循环依赖 |
| Core/BSP/keycontrol.c、keycontrol.h、motor.h | 直接包含实际依赖、明确无参函数声明；保留按键与 STEP/DIR 实现 |
| tests/test_communication.c、tests/stubs/*、tests/run.sh | 使用生产源码的主机侧 HAL 模拟、回归测试 |
| 本文档 | 取代原先与当前实现不一致的说明 |

CubeMX 初始化、USART/DMA 中断入口、GPIO 映射没有修改。PA2/PA3、115200 8N1、
RX Stream5 Circular、TX Stream6 Normal、UART/DMA IRQ 同为抢占优先级 6，均已静态核对。
`RS485_EN_GPIO_Port/Pin` 在 main.h 映射 PG8；高发送、低接收仍需实测确认。

## 核心流程

1. main 只调用 RS485_Proc、ZDT_X42S_Proc、Debug_Proc；TIM6 按键采样保留。
2. ZDT 检查事务占用、地址和恢复隔离状态，清理已入软件队列的旧数据，组帧并启动 DMA。
3. RS485 复制发送数据到 128 字节内部缓冲区，拉高方向，异步发送。
4. Normal DMA 完成后 HAL 开启 UART TC 中断；最后停止位结束时才调用 TxCplt，切回接收。
5. RX DMA 使用 128 字节循环缓冲；HT/TC/IDLE 回调读取实时 NDTR，将新字节搬到 256 字节队列（可用255字节）。
6. ZDT 每轮最多取32字节，按功能码和固定长度寻找完整帧，再匹配地址、当前功能及事务状态。
7. 每次只有一个等待回复的事务。RS485_Read 只能由 ZDT_Proc 调用；应用读取快照/公开结果。

DMA 回调使用实时写游标，避免延迟的 HT Size 在较晚 IDLE 之后被误当成回绕；重复游标不重复入队。
HT/TC 中断必须保留，DMA 和 UART 回调不能互相抢占。连续满速接收时中断服务间隔应小于半缓冲时间
（约5.56ms）；如果整圈数据在 CPU 处理前已被覆盖，单靠 NDTR 无法恢复或可靠检测丢失。
软件队列溢出可以检测：返回 RX_OVERFLOW，丢弃本轮副本，并使协议进入恢复隔离。

`RS485_ClearRx()` 在临界区只把软件 tail 移到已发布的 head，并清除对应溢出标志。
它不触碰 s_dma_last_pos，不停 DMA，不能清理尚未由回调搬运的字节，也不能消除将来的迟到回复。
TX 中清理返回 BUSY。错误/超时锁定 FAULT，晚到的完成回调不能恢复 READY。
硬件故障不提供自动重新初始化，检查原因后复位 MCU。

## 帧与结果

| 功能 | 请求 | 正常回复长度 |
|---|---|---|
| 版本 | Addr 1F 6B | 7：地址、1F、固件高低字节、硬件信息、硬件版本、6B |
| 电机状态 | Addr 3A 6B | 4：地址、3A、状态位、6B |
| 选项 | Addr 1A 6B | 4：地址、1A、选项位、6B |
| 实时位置 | Addr 36 6B | 8：地址、36、正负号、32位大端幅值、6B |
| Emm 位置 | 见下文 | 4：地址、FD、确认状态、6B |

版本示例 `01 1F 00 CE 23 14 6B` 对应 FW=206、硬件系列2、类型3、版本20。
`2 '\002'` 是 GDB 对8位值的数值/字符双显示。

3A 的数据是**位图**，不能把其中02或E2误当确认码：
bit0 enabled；bit1 arrived；bit2 stalled；bit3 stall_protection；
bit4 left_limit_level；bit5 right_limit_level；bit7 power_marker。
左右限位是输入电平，并不直接等于“触发”，应结合实际限位设置。
掉电标记默认0，可由电机命令置1，掉电后恢复0；本工程不修改它，因此不能仅凭0判定发生过掉电。

命令确认码：02 接收正确；E2 参数/执行条件错误；EE 格式错误；9F 主动完成通知；
12/22 表示未运动的相关零点/限位条件，按拒绝处理并保留原码。
`comm_result` 是主机通信结果；电机拒绝时它仍可能是 OK，必须同时检查 transaction 和 reply_status。
`reply_status_valid` 区分是否存在确认码。数据查询不会把状态数据当确认码。

流解析使用8字节滑动窗口，按功能识别长度，搜索每个可行后缀，跳过前导噪声和错位；
匹配不了的帧只计数，不完成事务。固定6B不具有CRC的检错能力，噪声伪造合法地址/功能/尾字节仍可能误判。
本实现覆盖上表的数据帧以及4字节确认帧；没有实现全部厂商命令的数据长度。
不要开启电机定时自动回传其他类型数据，单电机第一版也不支持本机 TX 自回显的收发器接法。

## 事务与运动

事务：IDLE → WAIT → SUCCESS / REJECTED / TIMEOUT / IO_ERROR。
响应超时100ms；RS485 TX超时30ms。无符号时差支持tick回绕。
超时/读取错误设置 resync_required，禁止直接重发。
该协议没有序号，同地址、同功能的旧回复不能与新回复严格区分。
只有在操作者确认电机已停止、旧会话已结束（必要时复位电机）后，才可显式恢复会话；
恢复只清软件队列与协议缓存，未来迟到回复的风险依然存在。硬件 FAULT 需要检查后复位 MCU。

Emm 帧13字节：`Addr FD Dir SpeedH SpeedL Acc P3 P2 P1 P0 Mode 00 6B`。
API 使用输入脉冲数，不做未经确认的角度换算；速度1..3000RPM（本版拒绝0），加速度0..255档位，
方向0/1，模式0/1/2，脉冲1..UINT32_MAX，运动监视期限100..120000ms。
模式0相对前一目标，1绝对坐标，2相对当前实时位置；同步固定00立即执行。
加速度0表示直接启动；档位越大加速越快，并非物理加速度单位。
宽参数检查之后才窄化或按大端序拆字节。

开始运动前，必须在同一地址得到1秒内的选项、状态、位置读回：
选项bit1=1（Emm）、bit2=1（闭环）、bit7=0（不缩小输入），已使能、前一目标已到达、无堵转/保护。
实际电机已由用户确认 Emm，选项读回作为运行时交叉核对；代码不会修改固件/闭环/缩放/使能参数。
限位电平不用于猜测限位有效极性，E2/12/22拒绝码原样保留。
运动进行中拒绝第二条运动及切换地址，只允许同地址只读事务。

运动证据：
- 发送启动OK：只证明驱动接受发送；UART TC只证明停止位发送完。
- FD 02：command_accepted=true，不意味着已到达。
- 确认后，实时位置相对运动前基线变化超过16编码器单位：started_observed=true。
  这是位置变化证据，不能独自证明变化一定来自本条命令，也不代表达到指定目标。
- 状态由未到达再到达，或运动期间收到FD 9F：motion=ARRIVED，arrived=true。
- 自动交替轮询3A与36，每50ms发起一条，仍遵守单事务；不在循环中延时。
- 很短的运动可能错过未到达状态，若应答配置又没有9F，会保持未证实并最终监视超时；不伪报完成。
- 堵转、失能、监视超时或通信错误后停止自动调度，状态保留为FAULT/TIMEOUT/UNKNOWN。
  **停止监视不等于电机急停**，本版没有发送停止或使能命令。

## VS Code 实机步骤

仅接一台电机；先核对PG8极性、A/B、共地、供电、终端、固定6B自由协议及电机应答设置。
测试期间断点暂停会停止主循环并可能让DMA溢出；电机可能继续运动。运动中优先用连续观察和外部安全断电手段。
观察 `'main.c'::s_debug`（或展开变量）。

`request` 是一次性请求，处理后置0，失败不自动重试。暂停时填写参数，继续运行一小段后再暂停看结果。
上电不会自动发任何查询或运动；初始化稳定后再开始测试。

| request | 动作 |
|---|---|
| 1 | 向 address 查询版本 |
| 2 | 查询电机状态 |
| 3 | 查询选项（核对Emm、闭环、缩放） |
| 4 | 查询实时位置 |
| 5 | 显式恢复会话，仅在已经人工确认会话干净且电机停止后使用 |
| 6 | 显式小步相对运动，必须同时满足arm与读回前置条件 |

1. `address=1, request=1`，继续运行，确认 result=OK、zdt.transaction=SUCCESS、version_valid=true，查看7字节原帧。
2. `request=2`，确认状态帧4字节、enabled与arrived符合电机实际，逐一验证限位电平及堵转标志。
3. 运动前人工核对步距角与细分。只有确认一圈3200输入脉冲时，80脉冲才等于9°；代码不假定这一设置。
4. 依次发送request=3、2、4，每次等SUCCESS。各读回到运动发送的运行时间必须不超过1秒；调试暂停期间HAL tick通常不走。
5. 初测可用 `direction=0, pulses=1, speed_rpm=5`；确认实际位移安全后逐步增加。
   设置 `arm=0x454D4D31, request=6`，然后继续运行。pulses初值0会拒绝发送；arm用后立即清零。
6. 小步接口只允许1..100脉冲、1..10RPM、方向0/1，加速度10档、模式2、监视10秒。
   检查 zdt.command_accepted、started_observed、saw_not_arrived、motion、arrived，与实物和位置读回交叉核对。
7. 再次测试必须重新取得新鲜读回并重新arm；不会自动重复运动，也不会自动使能。
8. 一般位置API为 ZDT_X42S_StartEmmMove，返回发送启动结果；仅在上述小步验证后由应用显式调用。

主要观察：
- rs485.state / last_hal_error / last_hal_status / last_error
- rs485.last_tx_start_result / last_tx_result / tx_completed / tx_abort_failed
- rs485.rx_bytes / overflow_count / queued_bytes
- zdt.transaction / comm_result / last_start_result / resync_required
- zdt.address / function / reply_status / reply_status_valid
- zdt.last_frame_len / last_frame / frames / ignored_frames
- zdt.version / status / options / position 及其valid、tick字段
- zdt.motion / command_accepted / started_observed / arrived

## 软件验证与硬件待验证

执行 `cmake --build --preset Debug`；主机回归执行 `sh tests/run.sh`。
测试直接编译生产驱动与协议源码，HAL/DMA模拟运行，启用-Wall -Wextra -Werror与Address/Undefined sanitizers。
覆盖DMA半满/满/回绕/重复与旧Size、队列溢出与清理、噪声错位/分包/错误地址、
版本解析、3A位图与ACK区分、事务互斥、迟到回复、超时隔离、Emm命令编码、参数拒绝、
02与运动/到位区分、异步9F、拒绝码与硬件故障锁定。

必须实测：
- 示波器确认PG8在最后停止位之后拉低，电机最短应答间隔内完成收发切换。
- 持续接收跨越128/256边界，验证回调频率、中断延迟与长时间稳定性；软件模拟不能证明总线时序。
- UART噪声/断线、RX溢出、TX超时及断电后错误记录，复位后的恢复流程。
- 实际固件对1A返回长度是否与手册4字节表一致（手册正文另提bit8/9，存在表述不一致；本版不使用这些位）。
- 选项缩放、步距角/细分、限位有效极性、掉电标记策略、命令应答配置与9F行为。
- 单步低速方向/位移、02确认、实际位置变化、未到达→到达、拒绝、堵转与监视超时。
- 小运动遗漏中间状态时，确认9F开启或接受“未证实完成”的结果。

尚未连接实际MCU/电机进行烧录和运行；所有实机结论需按以上步骤确认。

## 暂停落在 memcpy 与旧 Watch 兼容

手动暂停显示 SIGINT 属于正常暂停，并不等于 HardFault。若调用栈为 memcpy →
ZDT_X42S_GetDebug → Debug_Proc → main，这是调试快照复制。工具链中的 memcpy.c
源码没有随工程提供，编辑器会提示文件不存在；不应创建一个空文件代替库源码。
可以选择调用栈中的 main/Debug_Proc 查看工程源码，或使用“跳出”(Shift+F11)。
需要稳定查看主循环时，在 main 中 Debug_Proc() 调用处设置断点，再继续运行到断点。

快照现最多每50ms刷新一次，显式请求会立即刷新，协议处理仍每轮执行。
已恢复原有 Watch 名称：'zdt_x42s.c'::s_query.state、rx_len、comm_result、version，
以及 main 的 s_dbg_state、s_dbg_version_ok、s_dbg_version、s_dbg_comm_result。
state 表示版本查询状态；其他事务看 transaction。rx_len 在等待中表示滑动窗口字节数
（可能包含噪声），结束后表示最近帧长度。新快照继续使用 'main.c'::s_debug。

修改后先停止原调试会话，重新下载新ELF再启动，避免目标固件与调试符号不一致。
上电不会自动查询，需 request=1 显式触发版本查询；初值0/IDLE不是通信失败。
