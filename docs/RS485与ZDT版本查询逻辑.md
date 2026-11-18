# Emm 单电机控制：当前实现与实机调试

本说明对应本次依据用户提供 STM32F407 HAL/Emm 例程完成的重构。
例程选择、对照结论和来源摘要见 [例程对照与重构说明](例程对照与重构说明.md)。

## 先使用哪些入口

烧录 `build/Debug/02_MOTORCONTROL.elf`，启动后等待至少500ms。
上电不查询、不使能、不运动。物理按键按下并松开触发一次，长按不重复。

| 操作 | 行为 |
|---|---|
| K1 / PE3 | 地址1、方向0、80输入脉冲、5RPM、加速度0、相对上一目标 |
| K2 / PE2 | 地址1、方向1，其他参数相同 |
| K0 / PE4 | 优先发送软件停止请求，取消本机待执行的准备流程 |

两个运动键同时触发忽略；K0优先于其他键和Watch运动请求。忙时按键不排队。
80是**输入脉冲数**；仅在1.8°电机、16细分、一圈3200输入脉冲时约等于9°。
实际MStep尚需确认，机械条件允许后再按键。

按键参数在 `Core/Src/main.c` 的 USER CODE PD 中：

```c
#define APP_MOTOR_ADDRESS 1U
#define APP_JOG_PULSES    80U
#define APP_JOG_RPM       5U
#define APP_JOG_ACCEL     0U
#define APP_JOG_MODE      0U
```

K0是通讯软件停止，不是硬件急停。暂停MCU调试不能让电机停止。
FE确认仅表示电机接受停止请求，不能单独证明转子已经静止。

## 单一所有者与分层

| 文件 | 职责 |
|---|---|
| rs485.c/h | PG8方向控制、USART2 DMA收发、循环DMA到软件队列、错误锁定、快照 |
| emm_v5.c/h | 根据厂家 Emm_V5.c 编码位置/使能/停止/读取帧；无HAL、无UART访问 |
| zdt_x42s.c/h | 唯一RS485_Read消费者，帧解析、单事务、确认/超时、位置与到位证据 |
| motor_app.c/h | 唯一应用调度者，500ms启动等待、显式请求、使能和运动准备流程、停止和结果 |
| main.c | 初始化、按键与Watch请求路由、50ms调试快照 |
| keycontrol.c/h | TIM6每10ms采样、消抖、原子取事件；原KEY_Proc仍保留但不调用 |
| motor.c/h | 原STEP/DIR模块保留，不参与当前RS485业务 |

主循环固定为：

```c
RS485_Proc();
ZDT_X42S_Proc();
Requests_Proc();
MOTOR_AppProc();
Debug_Proc();
```

所有公开业务入口均为主循环调用，其他模块不能直接抢读RS485队列。
中断只搬字节、更新驱动状态或采样按键，不做协议解析或运动准备。

## 显式运动流程

```mermaid
flowchart LR
  R[按键或Watch请求] --> O[1A核对Emm闭环和缩放]
  O --> E[F3 AB显式使能并等待确认]
  E --> S[3A核对使能与堵转状态]
  S --> P[36取得运动前实际位置]
  P --> M[FD位置控制]
  M --> A[等待02或9F]
  A --> V[监视位置变化和到位]
```

使能由本次明确的运动请求触发，不在上电时自动使能。
各准备命令在上条回复后间隔10ms发起，非阻塞；单回复期限100ms。
UART发送超时仍为30ms。任何失败都结束本次操作，不自动重发运动。

到位标志Prf_TF是输出观察，不再当成首次运动必须成立的许可条件。
运动进行中禁止第二条运动；运动超时、通信不确定或故障后禁止直接重发。
使能、选项、位置数据必须有效且在1秒内；自动流程负责读新数据。
F3发送成功后使能状态缓存失效；FD发送成功后，运动前的状态与实际位置缓存失效，
基线单独保存在position_before_move中。FE发送成功后状态、实际位置和目标位置缓存失效。
切换地址时，包括FE停止路径在内，所有地址相关缓存均失效。

运动确认后，查询发送间隔和距上一匹配回复的间隔均至少50ms，循环3A、36、33、24、27、34，分别获取状态、
实际位置、最终目标位置、总线电压、相电流和轨迹实时设定位置。一次只有一个等待回复的事务。
这也保证较慢的FD确认不会在同轮被自动查询覆盖，应用层能先处理本次运动确认。

## Watch：先看应用结果

保留 `'main.c'::s_debug`。以下字段含义不同：

| 字段 | 应如何理解 |
|---|---|
| `result` | 最近一次新请求的接收结果，重复按键可能为BUSY |
| `app.last_request_result` | 最近一次真正交给应用的请求结果 |
| `app.result` | 当前/上次执行操作的结果；第二次按键的BUSY不会覆盖它 |
| `app.stage` | 当前执行到哪一步 |
| `app.active` | 是否还在准备、等待回复或监视运动 |
| `app.failed_stage`、`app.error` | 最后失败的阶段和分类 |
| `app.move` | 触发时捕获的参数；后续按键或Watch修改不会改变本次动作 |
| `zdt.transaction` | 最近一条通信事务的结果，不代表整个运动结果 |
| `zdt.move_reject` | 发送位置命令前被协议层拒绝的具体原因 |

正常应用阶段：
`BOOT_WAIT → IDLE → OPTIONS → ENABLE → STATUS → BASELINE → MOVE_ACK → MONITOR → DONE`。
如果直接收到9F，可从MOVE_ACK直接进入DONE。
失败进入FAILED。软件停止被接受进入STOP_ACCEPTED，不能把它解释成已经实测静止。

旧 `zdt.preparation/preparation_result` 已移入应用层，用 `app.stage/app.result` 替代。
旧 `s_query.state/rx_len/comm_result/version` 与 main 的 `s_dbg_*` 版本监视仍保留。
`state`只指版本查询；非版本业务请看transaction和app.stage。

Watch显式请求：设置参数，最后写request，然后连续运行。

| request | 含义 |
|---|---|
| 1 / 2 / 3 / 4 | 版本 / 电机状态 / 选项 / 实际位置 |
| 5 | 人工确认旧会话干净、电机停止后的协议恢复；不发送电机指令 |
| 6 | 完整单次位置流程，需`arm=0x454D4D31`，用后清零 |
| 7 / 8 / 9 | 总线电压 / 相电流 / 目标位置，只读 |
| 10 | 软件停止请求，允许优先中断普通查询/准备流程 |
| 11 / 12 | 单独使能 / 失能，只操作使能，不发送位置命令 |
| 13 | 只读3B：编码器、校准、回零、过热/过流状态，结果见`zdt.health` |
| 14 | 只读21：Emm PID，结果见`zdt.pid`；先执行request=3核对本地址Emm |
| 15 | 只读42：Emm驱动配置，结果见`zdt.config`；先执行request=3 |
| 16 | 只读23：积分限幅，结果见`zdt.integral_limit`和`integral_limit_valid` |
| 17 | 只读34：轨迹实时设定位置，结果见`zdt.setpoint_position/setpoint_valid`；运动监视时自动轮询 |

request=6读取address、direction、pulses、speed_rpm、acceleration、mode、timeout_ms。
默认80脉冲、5RPM、acceleration=0、mode=0、timeout_ms=10000。
位置模式0相对上一输入目标，1绝对目标，2相对当前实际位置。
校准、恢复出厂、修改固件、修改PID/电流均未暴露为按键或上电动作。

从业务代码触发一次：

```c
const ZDT_EmmMove_t move = {1U, 0U, 5U, 0U, 80U, 0U, 10000U};
RS485_Result_t accepted = MOTOR_AppMove(&move);
```

返回OK表示应用接受请求，不等于FD已经发出或电机到位。
不要放在while每轮调用；放在明确事件中，随后持续调用主循环任务。

## 实际发帧和回复

默认按键位置帧：`01 FD 00 00 05 00 00 00 00 50 00 00 6B`。
使能帧：`01 F3 AB 01 00 6B`；失能帧：`01 F3 AB 00 00 6B`。
停止帧：`01 FE 98 00 6B`。

| 功能 | 正常回复长度 |
|---|---|
| 1F版本 | 7字节 |
| 1A选项 | 5字节；本台实机确认`01 1A 00 06 6B`，16位大端选项 |
| 3A状态 | 4字节，第三字节为位图，不能当确认码 |
| 36实际位置 / 33最终目标位置 / 34轨迹实时设定位置 | 8字节，符号+32位大端幅值 |
| 24电压 / 27相电流 | 5字节，16位大端数据 |
| 3B编码器/校准/保护状态 | 4字节，第三字节为位图 |
| 21 Emm PID / 23积分限幅 | 15 / 7字节，三个 / 一个32位大端整数 |
| 42 Emm驱动配置 | 33字节，含`21 15`长度/参数个数；请求为`Addr 42 6C 6B` |
| F3 / FD / FE确认 | 4字节，02接受、9F完成通知、E2/EE拒绝，保留12/22原码 |

流解析识别明确的功能码和长度，优先保留最早的合法候选头，等待该帧完整后再解析，
避免把位置等数据中的`01 FD 9F 6B`误当作独立完成通知。只有候选头、符号、确认码或帧尾不合法时
才逐字节重新寻找帧头；伪候选头最多等待33字节即可检验。支持前导噪声、错位、分包。
无关地址/功能不会完成当前事务。固定6B没有CRC的检错能力；不支持任意未知数据帧。

关键证据：`zdt.last_tx/last_tx_len`为最近发送，`move_frame[13]`为本次运动帧，
`move_reply[4]/move_reply_valid`保留运动确认，不会被后续查询覆盖。
`command_accepted`仅说明FD被接受；`started_observed`需要位置变化超过16编码器单位。
`position_before_move/position_delta`与`target_position`用于比对。
到位要求观察到未到位→到位，或收到当前动作FD 9F。很短运动若错过中间状态且没9F，
可能完成未证实并监视超时，不伪报到位。建议电机Response设置为Both，以取得确认和完成通知。

## 失败、停止与恢复

普通通信超时/溢出/硬件错误保留resync_required，禁止直接重发。
停止请求在当前UART发送结束后发FE；原逻辑事务被取消，旧回复不完成FE事务。
如果中断了等待回复的事务，或会话本就不确定，FE确认后仍要求显式恢复。
若取消的是版本查询，旧版本查询状态转为ERROR，不再停留在WAIT_RESPONSE。
协议没有事务序号，恢复不能识别未来同地址同功能码的迟到回复。

恢复仅清已入软件队列的数据和协议缓存，不重启循环DMA、不重置DMA读游标。
未由DMA回调搬运的字节、未来的迟到回复不在清理范围。硬件FAULT需查明原因后复位MCU。
运动监视超时不等于电机已停止；需要操作者确认，必要时显式请求停止。

## 硬件与已知限制

继续使用USART2 PA2/PA3、115200 8N1、RX DMA1_Stream5 Circular、TX DMA1_Stream6 Normal。
PG8使用RS485_EN宏，高发送、低接收。只有UART TC后才释放发送方向。
本次未复制例程的USART1/PA9/PA10、168MHz HSE配置，也未改CubeMX初始化。

RX缓冲128、队列256（可用255）、TX缓冲128。HT/TC/IDLE回调均保留，USART2与RX DMA
抢占优先级相同；用实时NDTR避免旧HT Size导致重复回绕。持续满速流下应在约5.56ms内服务半缓冲，
整圈覆盖后无法靠NDTR恢复丢失数据。软件溢出可检测并报告。

实机仍需确认：MStep/步距角、校准、供电电流能力、电机绕组连接、限位有效极性、
电流/电压限制、到位窗口和Response。之前现场11.5V、相电流15mA、目标更新但转子未跟随，
不能仅凭这几个读回认定硬件损坏或已经修好。此重构尚未烧录测试，不自动校准或改电机参数。
厂家接线图示例标注10–29V、3A以上电源，实际选型应对照本台电机和板卡。

## 目标更新但无位移时的只读诊断

2026-11-16用户调试截图：`command_accepted=true`，`position_before_move=4`，
`position_delta=0`，`target_position=3268`，`bus_voltage_mv=11490`，
`phase_current_ma=17`，目标/电压/电流均有效，`started_observed=false`。
目标与实际相差3264编码单位，约17.93°，说明收到命令和产生实际位移是两个不同结果。
这些反馈支持继续核查校准、PID、输出限制、绕组连接与驱动输出，尚不足以认定具体故障。

本次新增request=13～16，仅发送只读查询。烧录新Debug固件后不按运动键，等待500ms，
依次将`'main.c'::s_debug.request`设为3、13、14、15、16，每次连续运行至`app.stage=DONE`，
再写下一项，最后查看：

- `zdt.health`：`valid=true`后检查`encoder_ready`、`calibrated`及`over_temperature/over_current`。
- `zdt.pid`：`valid=true`后记录`kp/ki/kd`；不要根据默认值盲目写回参数。
- `zdt.config`：`valid=true`后记录`pulse_mode`、`enable_level`、`microsteps`、`max_current_ma`、
  `max_output_raw`和`response_mode`。`max_output_raw`保留原始参数值，手册不同章节的单位倍数不一致。
- `zdt.integral_limit`：同时确认`integral_limit_valid=true`。

健康状态不就绪时先定位具体标志；参数为零或明显异常时与电机适用配置核对。
参数正常仍不输出时，断电检查电机两相绕组连接及接插件，再进行驱动与电机的独立验证。
查询入口不会校准、清保护、改PID、提高电流限制或发送运动。
如果在旧会话已发生运动/通信超时，应用仍保留原锁定策略；不要为了读取诊断绕过不确定运动状态。

进一步反馈：用户确认request=11后锁力增大；失能后手动转轴，实际位置从2061变为3901，
变化1840单位约10.1°，与用户观察的11.3°→21.3°一致。健康状态raw=3，编码器和校准均就绪，
未回零、未过热/过流。PID读回18000/20/1024000，积分限幅131072；配置为Hold、16细分、
最大电流3000mA、输出限制原值5000。参数与部分手册默认值不同，尚未据此判定错误或写回。

为区分最终目标已接收但轨迹未推进，或轨迹已推进但闭环未跟随，新增34只读查询和自动轮询。
一次明确运动请求后约2秒，在有效位为true时同时比较target_position、setpoint_position和position，
并查看phase_current_ma及status.enabled。最终目标与轨迹位置不同可以提示轨迹尚在推进或被中断；
轨迹已到目标但实际位置不跟随则进一步查反馈匹配、闭环参数及驱动输出。
单次34读回不能直接证明内部故障，也不能单凭某个PID值偏离手册默认值就覆盖参数。

## 验证命令

```sh
cmake --build --preset Debug --clean-first
sh tests/run.sh
python3 tests/check_vendor_reference.py
```

最后一项从用户提供RAR只读提取已核对的三个函数，重定向HAL发送到测试缓冲，
与生产编码器执行768组字节对比，不运行例程main或实际电机。
主机测试直接编译生产编码器、协议、应用和驱动，覆盖完整使能/位置流程、拒绝/超时、
重复请求BUSY不覆盖执行结果、停止抢占/迟到回复、DMA回绕/溢出、选项5字节、9F到位、
位置数据内嵌完成通知、慢ACK与轮询间隔、跨地址缓存失效、版本查询取消、
tick回绕、按键消抖，以及旧STEP/DIR接口的非法参数与脉冲数。软件模拟不能代替物理总线和电机验证。
