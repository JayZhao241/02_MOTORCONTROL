# 电机控制台

支持 macOS 和 Windows，使用 Python 3.10 及以上版本和 Tkinter。

## 在 VS Code 中运行项目

按 F5，选择 **运行项目：固件 + 桌面端**。统一入口 `run.py` 会构建 Debug 固件，通过 ST-Link 下载、校验并从 `0x08000000` 启动，再打开桌面端并选好调试器。桌面端打开后点击“连接”，再操作电机。

也可在工程根目录执行：

```sh
python3 run.py
```

Windows 使用 `py -3 run.py`。入口会自动寻找带 Tkinter 的 Python。固件工具从 PATH 或 STM32CubeCLT 安装目录查找。

仅查看界面时选择 F5 的 **运行桌面端：模拟**，或执行 `python3 run.py --simulate`。`--desktop-only` 会只打开 ST-Link 模式的桌面端；`--dry-run` 打印完整执行计划而不构建、下载或连接。多只 ST-Link 时用 `--probe 序列号` 指定设备。

## 启动

安装带 Tkinter 的 Python。模拟连接无需额外依赖；串口连接先安装串口依赖。

macOS：

```sh
python3 -m pip install -r desktop/requirements.txt
python3 -m desktop.app
```

也可以双击 `启动电机控制台.command`。脚本会寻找带 Tkinter 的 Python，包括 Homebrew、python.org、项目 `.venv` 和 `desktop/.runtime/venv`，不依赖 Finder 的 PATH。

Homebrew Python 若报 `No module named '_tkinter'`，安装与 Python 版本匹配的 Tk 支持。以 Python 3.14 为例：

```sh
brew install python-tk@3.14
```

可先检查启动环境：

```sh
./desktop/启动电机控制台.command --check
```

程序异常退出时，启动日志保存在 `desktop/.runtime/startup.log`。

Windows：

```powershell
py -3 -m pip install -r desktop/requirements.txt
py -3 -m desktop.app
```

也可以双击 `启动电机控制台.bat`。

以上命令在工程根目录运行。

## 连接

- **模拟连接**：本地模拟运动，适合查看界面和调试操作流程。
- **ST-Link 连接**：沿用 VS Code 下载/调试使用的 ST-Link，经 SWD 操作现有 F407 固件的 Watch 请求接口。选择调试器和对应的固件 ELF，再连接。电脑需安装 STM32CubeCLT，或在 PATH 中提供 `STM32_Programmer_CLI`、`arm-none-eabi-gdb` 和 `arm-none-eabi-objcopy`。默认 ELF 为 `build/Debug/02_MOTORCONTROL.elf`，可点“选择固件 ELF”更换。连接前核对板上 Flash 与 ELF 是否一致。
- **串口连接**：电脑通过自动收发的 USB-RS485 转换器直连 Emm 电机，选择串口、波特率、电机地址和每圈输入脉冲，再点击连接。默认 115200、地址 1、3200 脉冲/圈；按电机实际参数填写。

窗口上方始终显示“连接串口”栏。使用串口时选择“串口连接”，再从下拉列表中选择串口；列表显示完整路径、设备名称以及 USB / 系统串口分类。macOS 通常为 `/dev/cu.usbserial-*` 或 `/dev/cu.usbmodem*`，Windows 为 `COM*`。

未连接时每 2 秒刷新，也可点击“刷新串口”。只发现一个 USB 串口时自动选中；多个 USB 串口需要手动选择。蓝牙和调试系统串口不自动选中。可手动输入路径；断开后可以修改连接方式及参数。

ST-Link 是 USB 调试器，不一定提供串口。ST-Link 模式按序列号列出调试器，无需寻找 `/dev/cu.*` 或 `COM*`。板上的应用需运行到主循环；仅下载成功或停在断点时，固件不能处理桌面端请求。使用桌面端时结束 VS Code 的调试会话，以释放调试器。若提示 MCU 处于系统 Bootloader，检查 BOOT0 为低电平并复位，使固件从主 Flash 启动。

若提示“未发现 USB 串口”，先把 USB-RS485 转换器接入电脑；可通过拔插前后的列表变化确认对应接口。

## 操作

连接后点击“使能电机”。角度定位支持顺时针/逆时针单步、指定累计角度、返回 0°及保存开机零点。按当前电机的实际方向，顺时针对应角度减小，逆时针对应角度增大；连续旋转设置方向、转速和时长，解锁后启动。停止按钮和 Esc 可结束运动。

两种实机连接的连续旋转按 `转速 × 6 × 时长` 换算为有限脉冲行程，使用现有 FD 定位指令；电脑计时到期也会发送停止。剩余时间由电脑计时，转速由位置反馈估算。连接时只查询，不自动使能。操作结果查看右侧信息及日志。运动结束后会短暂显示“确认到位中”，补读最终反馈后返回待机；操作日志同时保存在 `desktop/.runtime/operations.log`。

通信边界、停止语义和未验证事项见 [防御性说明](防御性说明.md)。

## 固定开机零点

“保存为开机零点”将轴的当前物理位置存入驱动器，并启用上电单圈就近回零，回零速度为 5 RPM。保存成功后当前坐标清零；以后电机重新上电会回到此位置，不依赖电脑或桌面端在线。

平时无需重复保存。以后要修改时，将轴移到新的位置、停稳后，再点击一次“保存为开机零点”。打开桌面端、连接设备和运行 F5 都不会重写原点。模拟连接中的保存只影响模拟坐标。

## 验证

```sh
python3 -m unittest discover -s desktop/tests -v
```

Tk 界面测试需要带 Tkinter 的图形桌面环境。
