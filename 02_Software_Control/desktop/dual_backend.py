"""Two axes share one worker and one physical RS485 transport."""
from dataclasses import replace
import math
import queue
import threading
import time

from .controller import DemoBackend
from .serial_backend import SerialBackend


class DualDemoBackend:
    def __init__(self):
        self.axes = [DemoBackend(), DemoBackend()]
        self.selected = 0

    def snapshot(self):
        return self.axes[self.selected].snapshot()

    def connect(self):
        for axis in self.axes:
            axis.connect()

    def disconnect(self):
        for axis in self.axes:
            axis.disconnect()

    def update(self, seconds):
        for axis in self.axes:
            axis.update(seconds)

    def stop(self):
        for axis in self.axes:
            axis.stop()

    def move_pair(self, targets, rpm):
        for axis, target in zip(self.axes, targets):
            axis.move_to(target, rpm)

    def enable_all(self):
        for axis in self.axes:
            axis.enable(True)

    def __getattr__(self, name):
        return getattr(self.axes[self.selected], name)


class DualSerialBackend(SerialBackend):
    def __init__(self, port, baudrate=115200, pulses_per_turn=3200, *, serial_factory=None):
        super().__init__(port, baudrate, serial_factory=serial_factory)
        self.axes = [SerialBackend(port, baudrate, address, pulses_per_turn) for address in (1, 2)]
        self.selected = 0
        self.activity = "待机"
        self.command_queued_at = None
        self.command_started_at = None
        self._wake = threading.Event()
        self.transport_name = "USB-RS485"

    def snapshot(self):
        state = self.axes[self.selected].snapshot()
        if not self._commands.empty() and state.connected:
            return replace(state,motion="pending")
        return state

    def connect(self):
        self.activity = "正在准备通信接口"
        self.command_queued_at = time.monotonic()
        self._wake.clear()
        for axis in self.axes:
            axis._state = replace(axis._state, connected=False, enabled=False, error=None, motion="connecting")
        super().connect()

    def disconnect(self):
        self.activity = "正在停止两轴并关闭连接"
        super().disconnect()
        self._wake.set()
        for axis in self.axes:
            axis._state = replace(axis._state, motion="closing" if self._thread and self._thread.is_alive() else "idle")

    def _submit(self, command, *args):
        if not all(axis.snapshot().connected for axis in self.axes):
            raise ValueError("请先连接两台电机")
        index = self.selected
        affected = self.axes if command in ("stop", "pair", "enable_all") else [self.axes[index]]
        for axis in affected:
            axis._state = replace(axis._state, motion="pending")
        self.command_queued_at = time.monotonic()
        self.activity = {"stop":"停止请求优先处理中", "pair":"准备双轴定位", "move":"准备单轴定位", "enable":"正在设置电机使能", "enable_all":"正在使能两台电机", "startup_origin":"正在保存并校验开机零点", "origin":"正在设置坐标零点"}.get(command,"正在处理指令")
        self._commands.put((index, command, args))
        self._wake.set()

    def move_pair(self, targets, rpm):
        self._cancel_move.clear()
        self._submit("pair", targets, rpm)

    def enable_all(self):
        self._submit("enable_all")

    def rotate(self, direction, rpm, duration):
        self._cancel_move.clear()
        self._submit("move", self.snapshot().position_deg + direction*rpm*6*duration, rpm, duration)

    def stop(self):
        self._cancel_move.set()
        for axis in self.axes:
            axis._cancel_move.set()
        while True:
            try:
                self._commands.get_nowait()
            except queue.Empty:
                break
        if all(axis.snapshot().connected for axis in self.axes):
            self._submit("stop")

    def _stop_both(self):
        errors = []
        for axis in self.axes:
            try:
                axis._execute("stop", (), poll=False)
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError("；".join(errors))

    def _pair(self, targets, rpm):
        self.activity = "正在核对两轴配置与当前位置"
        prepared = []
        # Validate BOTH axes before caching either command.
        for axis, target in zip(self.axes, targets):
            if self._cancel_move.is_set() or self._exit.is_set():
                self._stop_both()
                return
            options = int.from_bytes(axis._exchange(0x1A, length=5)[2:4], "big")
            if options & 0x86 != 6 or not axis._status() & 1:
                raise RuntimeError(f"电机{axis.address}须使能并使用未缩放的 Emm 闭环模式")
            config = axis._exchange(0x42, b"\x6c", length=33)
            if config[2:4] != b"\x21\x15":
                raise RuntimeError(f"电机{axis.address}配置回复不是 Emm 33 字节格式")
            if config[22] != 1:
                modes = {0:"None", 1:"Receive", 2:"Reached", 3:"Both", 4:"Other"}
                mode = modes.get(config[22], str(config[22]))
                raise RuntimeError(f"电机{axis.address} Response={mode}，双电机共用 RS485 请将两台均设为 Receive；主动到位回报可能冲突。本次未发送定位指令")
            actual = axis._position()
            delta = target + axis._offset - actual
            pulses = math.floor(abs(delta)*axis.pulses/360+0.5)
            if pulses > 0xFFFFFFFF:
                raise ValueError("目标位移超出脉冲范围")
            prepared.append((axis, actual, delta, pulses))
        for axis, actual, delta, pulses in prepared:
            if self._cancel_move.is_set() or self._exit.is_set():
                self._stop_both()
                return
            payload = bytes((int(delta < 0),))+int(rpm).to_bytes(2,"big")+b"\x00"
            payload += pulses.to_bytes(4,"big")+b"\x02\x01"
            self.activity = f"正在缓存电机 {axis.address} 目标"
            axis._exchange(0xFD, payload)
            axis._state = replace(axis._state, target_deg=actual-axis._offset+math.copysign(pulses*360/axis.pulses,delta))
        if self._cancel_move.is_set() or self._exit.is_set():
            self._stop_both()
            return
        self.activity = "正在同步启动两台电机"
        packet = bytes.fromhex("00 FF 66 6B")
        if self._serial.write(packet) != len(packet):
            raise RuntimeError("同步触发发送不完整")
        for axis, actual, delta, pulses in prepared:
            axis._motion = "positioning"
            axis._state = replace(axis._state, motion="positioning")
            axis._move_deadline = time.monotonic()+abs(delta)/(rpm*6)+getattr(axis,"feedback_grace_s",5)
            axis._stable_since = None

    def _run(self):
        error = None
        try:
            factory = self.factory
            if factory is None:
                from serial import Serial
                factory = Serial
            self._serial = factory(self.port, self.baudrate, timeout=0.02, write_timeout=0.3)
            self.transport_name = getattr(self._serial, "transport_name", "USB-RS485")
            self._serial.reset_input_buffer()
            # SWD exchanges invoke external tools; allow for external-tool latency.
            if hasattr(self._serial, "link"):
                for axis in self.axes:
                    axis.feedback_grace_s = 30
            for axis in self.axes:
                self.activity = f"正在读取电机 {axis.address} 反馈"
                axis._serial = self._serial
                axis._commands = self._commands
                axis._offset = 0
                axis._previous = None
                axis._motion = "idle"
                axis._poll()
            poll_index = 0
            while not self._exit.is_set():
                try:
                    index, command, args = self._commands.get_nowait()
                except queue.Empty:
                    pass
                else:
                    self.command_started_at = time.monotonic()
                    if command == "stop":
                        self._stop_both()
                    elif command == "pair":
                        self._pair(*args)
                    elif command == "enable_all":
                        for axis in self.axes:
                            axis._execute("enable", (True,), poll=False)
                    else:
                        axis = self.axes[index]
                        axis._cancel_move.clear()
                        if command != "move" or not self._cancel_move.is_set():
                            axis._execute(command, args, poll=False)
                # Operator work always wins over the next background poll.
                if not self._commands.empty():
                    continue
                if any(a._deadline and time.monotonic() >= a._deadline for a in self.axes):
                    self._stop_both()
                axis = self.axes[poll_index % 2]
                axis._poll(telemetry=axis._motion == "idle")
                poll_index += 1
                if not self._commands.empty():
                    continue
                moving = any(a._motion != "idle" for a in self.axes)
                self._wake.wait(0.025 if moving else 0.10)
                self._wake.clear()

        except Exception as exc:
            error = str(exc)
        finally:
            if self._serial:
                try:
                    self._stop_both()
                except Exception as exc:
                    message = str(exc)
                    error = (error + "；停止请求未全部送达，不能确认两轴已停止") if error else "两轴停止未确认：" + message
                finally:
                    try:
                        self._serial.close()
                    except Exception as exc:
                        message = str(exc)
                        error = error or message
            for axis in self.axes:
                axis._state = replace(axis._state, connected=False, enabled=False, motion="idle", error=error,
                                      remaining_s=None, target_deg=None)
