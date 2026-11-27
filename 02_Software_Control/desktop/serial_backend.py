"""USB-RS485 adapter for the Emm protocol used by Core/BSP/emm_v5.c."""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
import json
from pathlib import Path
import queue
import threading
import time

from .controller import Snapshot, number


@dataclass(frozen=True)
class SerialPortInfo:
    device: str
    description: str
    is_usb: bool = False

    @property
    def label(self) -> str:
        kind = "USB" if self.is_usb else "系统串口"
        return f"{self.device}  ·  {self.description} ({kind})"


def serial_port_details() -> list[SerialPortInfo]:
    try:
        from serial.tools.list_ports import comports
    except ImportError:
        raise RuntimeError("缺少串口组件，请安装 desktop/requirements.txt") from None
    result = []
    for port in comports():
        usb = port.vid is not None or port.pid is not None or port.hwid.startswith("USB")
        description = port.description if port.description and port.description != "n/a" else "未命名设备"
        result.append(SerialPortInfo(port.device, description, usb))
    return sorted(result, key=lambda port: (not port.is_usb, port.device))


def serial_ports() -> list[str]:
    return [port.device for port in serial_port_details()]


class ReplyTimeout(RuntimeError):
    """No matching motor response; not proof of a broken USB connection."""
    def __init__(self, address, function, received):
        self.address, self.function, self.received = address, function, bytes(received)
        super().__init__(f"电机 ID {address} 回复超时：{function:02X}")


class SerialBackend:
    def __init__(self, port: str, baudrate: object = 115200, address: object = 1,
                 pulses_per_turn: object = 3200, *, serial_factory=None) -> None:
        if not port.strip():
            raise ValueError("请选择串口")
        self.port = port.strip()
        self.baudrate = self._integer(baudrate, "波特率", 1200, 2000000)
        self.address = self._integer(address, "电机地址", 1, 255)
        self.pulses = self._integer(pulses_per_turn, "每圈脉冲", 1, 10000000)
        self.factory = serial_factory
        self._state = Snapshot(False, False, 0, None, 0, "idle", None, "驱动坐标")
        self._commands: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._exit = threading.Event()
        self._cancel_move = threading.Event()
        self._serial = None
        self._offset = 0.0
        self._deadline: float | None = None
        self._move_deadline: float | None = None
        self._motion = "idle"
        self._stable_since: float | None = None
        self._previous: tuple[float, float] | None = None
        self._last_telemetry = 0.0

    @staticmethod
    def _integer(value, label, low, high) -> int:
        result = number(value, label, low, high)
        if not result.is_integer():
            raise ValueError(f"{label}请输入整数")
        return int(result)

    def snapshot(self) -> Snapshot:
        return self._state

    def connect(self) -> None:
        if self._thread and self._thread.is_alive():
            raise ValueError("串口正在连接或关闭")
        self._exit.clear()
        self._cancel_move.clear()
        self._commands = queue.Queue()
        self._offset = 0.0
        self._motion = "idle"
        self._deadline = self._move_deadline = self._stable_since = self._previous = None
        self._state = Snapshot(False, False, 0, None, 0, "connecting", None, "驱动坐标")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def disconnect(self) -> None:
        self._cancel_move.set()
        self._exit.set()
        if self._thread and self._thread.is_alive():
            self._state = replace(self._state, motion="closing")
        else:
            self._state = replace(self._state, connected=False, enabled=False, motion="idle")

    def _submit(self, command: str, *args) -> None:
        if not self._state.connected:
            raise ValueError("请先连接设备")
        if self._state.motion in ("closing", "connecting"):
            raise ValueError("正在切换连接")
        self._state = replace(self._state, motion="pending")
        self._commands.put((command, args))

    def enable(self, enabled: bool) -> None:
        self._submit("enable", enabled)

    def move_to(self, angle: float, rpm: float) -> None:
        self._cancel_move.clear()
        self._submit("move", angle, rpm, None)

    def rotate(self, direction: int, rpm: float, duration: float) -> None:
        self._cancel_move.clear()
        # A finite position trajectory uses only the verified FD command.
        self._submit("move", self._state.position_deg + direction * rpm * 6 * duration, rpm, duration)

    def stop(self) -> None:
        self._cancel_move.set()
        if not self._state.connected:
            return
        while True:
            try:
                self._commands.get_nowait()
            except queue.Empty:
                break
        self._submit("stop")

    def set_origin(self) -> None:
        self._submit("origin")

    def save_startup_origin(self) -> None:
        self._submit("startup_origin")

    def update(self, seconds: float) -> None:
        pass  # Serial I/O and its clock belong to the worker.

    READ_FUNCTIONS = frozenset((0x1A,0x22,0x24,0x27,0x33,0x34,0x36,0x3A,0x3B,0x42))

    def _record_timeout(self, error, retry):
        try:
            path = Path(__file__).resolve().parent / ".runtime" / "communication_errors.jsonl"
            path.parent.mkdir(parents=True,exist_ok=True)
            state = getattr(self._serial,"last_exchange_state",None)
            diagnostics = {k:(v.hex() if isinstance(v,bytes) else v) for k,v in state.items()} if isinstance(state,dict) else None
            with path.open("a",encoding="utf-8") as stream:
                stream.write(json.dumps({"time":time.strftime("%Y-%m-%d %H:%M:%S"), "address":error.address,
                    "function":f"{error.function:02X}","rx":error.received.hex(),"retry_read":retry,
                    "motion":self._motion,"bridge":diagnostics},ensure_ascii=False)+"\n")
        except OSError:
            pass

    def _exchange(self, function: int, payload: bytes = b"", length: int = 4) -> bytes:
        try:
            return self._exchange_once(function,payload,length)
        except ReplyTimeout as error:
            retry = function in self.READ_FUNCTIONS and not self._exit.is_set() and self._commands.empty()
            self._record_timeout(error,retry)
            if not retry:
                raise
            if self._exit.wait(0.02):
                raise error
            # Only read-only queries get one bounded retry. Never replay FD,
            # enable, stop, homing, or a persistent configuration write.
            try:
                return self._exchange_once(function,payload,length)
            except ReplyTimeout as final:
                self._record_timeout(final,False)
                raise

    def _exchange_once(self, function: int, payload: bytes = b"", length: int = 4) -> bytes:
        packet = bytes((self.address, function)) + payload + b"\x6b"
        if self._serial.write(packet) != len(packet):
            raise RuntimeError("串口发送不完整")
        deadline = time.monotonic() + (2 if function in (0x93, 0x4C) else 0.35)
        buffer = bytearray()
        received = bytearray()
        lengths = {0x42: 33, 0x22: 18, 0x93: 4, 0x4C: 4, 0x0A: 4, 0x3B: 4, 0x1A: 5, 0x24: 5, 0x27: 5, 0x33: 8, 0x34: 8, 0x36: 8,
                   0x3A: 4, 0xF3: 4, 0xFD: 4, 0xFE: 4}
        while time.monotonic() < deadline:
            chunk = self._serial.read(64)
            received.extend(chunk)
            buffer.extend(chunk)
            while len(buffer) >= 2:
                size = lengths.get(buffer[1])
                if buffer[0] != self.address or size is None:
                    del buffer[0]
                    continue
                if len(buffer) < size:
                    break
                frame = bytes(buffer[:size])
                if frame[-1] != 0x6B:
                    del buffer[0]
                    continue
                del buffer[:size]
                if frame[1] != function:
                    continue
                if function in (0xF3, 0xFD, 0xFE, 0x93, 0x4C, 0x0A) and frame[2] not in (0x02, 0x9F):
                    raise RuntimeError(f"电机拒绝指令：{frame[2]:02X}")
                if size != length:
                    raise RuntimeError("回复长度错误")
                return frame
        raise ReplyTimeout(self.address,function,received)

    def _position(self, function=0x36) -> float:
        frame = self._exchange(function, length=8)
        if frame[2] not in (0, 1):
            raise RuntimeError("位置回复符号错误")
        return (-1 if frame[2] else 1) * int.from_bytes(frame[3:7], "big") * 360 / 65536

    def _status(self) -> int:
        status = self._exchange(0x3A)[2]
        if status & 0x0C:
            raise RuntimeError("电机堵转或堵转保护")
        return status

    def _execute(self, command, args, *, poll=True) -> None:
        if command == "stop":
            self._exchange(0xFE, b"\x98\x00")
            self._motion = "stopping"
            self._deadline = self._move_deadline = None
            self._state = replace(self._state, target_deg=None)
            self._stable_since = None
        elif command == "enable":
            self._exchange(0xF3, bytes((0xAB, int(args[0]), 0)))
            self._motion = "idle" if args[0] else "stopping"
            self._deadline = self._move_deadline = None
        elif command == "startup_origin":
            options = int.from_bytes(self._exchange(0x1A, length=5)[2:4], "big")
            if options & 6 != 6 or options & 128:
                raise RuntimeError("保存开机零点需要 Emm 闭环且未启用输入缩放")
            self._status()
            health = self._exchange(0x3B)[2]
            if health & 0x0F != 3:
                raise RuntimeError("编码器/校准未就绪，或电机正在回零")
            first = self._position()
            self._exit.wait(0.2)
            if self._exit.is_set():
                return
            if abs(self._position()-first) > max(360/65536, 360/self.pulses):
                raise RuntimeError("电机尚未停稳，未保存开机零点")
            raw = bytearray(self._exchange(0x22, length=18)[2:17])
            if raw[0] > 3 or raw[1] > 1 or raw[14] > 1:
                raise RuntimeError("回零参数格式不匹配")
            raw[0] = 0
            raw[2:4] = (5).to_bytes(2, "big")
            raw[4:8] = (10000).to_bytes(4, "big")
            raw[14] = 1
            self._exchange(0x93, b"\x88\x01")
            time.sleep(0.25)
            self._exchange(0x0A, b"\x6d")
            self._exchange(0x4C, b"\xae\x01"+raw)
            time.sleep(0.25)
            if self._exchange(0x22, length=18)[2:17] != raw:
                raise RuntimeError("开机回零参数读回不一致")
            self._offset = 0
            self._previous = None
            self._state = replace(self._state, origin="固定开机零点", target_deg=None)
            self._motion = "idle"
        elif command == "origin":
            self._offset = self._position()
            self._previous = None
            self._state = replace(self._state, origin="用户坐标", target_deg=None)
        elif command == "move":
            angle, rpm, duration = args
            options = int.from_bytes(self._exchange(0x1A, length=5)[2:4], "big")
            if options & 6 != 6 or options & 128:
                raise RuntimeError("电机须使用 Emm 闭环模式并关闭输入缩放")
            if not self._status() & 1:
                raise RuntimeError("电机未使能")
            actual = self._position()
            delta = angle + self._offset - actual
            pulses = math.floor(abs(delta) * self.pulses / 360 + 0.5)
            if pulses == 0:
                self._state = replace(self._state, target_deg=actual - self._offset)
                self._motion = "idle"
                if poll:
                    self._poll()
                return
            if pulses > 0xFFFFFFFF:
                raise ValueError("目标位移超出脉冲范围")
            payload = bytes((int(delta < 0),)) + int(rpm).to_bytes(2, "big") + b"\x00"
            payload += pulses.to_bytes(4, "big") + b"\x02\x00"
            if self._cancel_move.is_set() or self._exit.is_set():
                return
            self._motion = "positioning"
            self._exchange(0xFD, payload)
            target = actual - self._offset + math.copysign(pulses * 360 / self.pulses, delta)
            self._state = replace(self._state, target_deg=target)
            self._motion = "continuous" if duration is not None else "positioning"
            now = time.monotonic()
            self._deadline = now + duration if duration is not None else None
            self._move_deadline = now + abs(delta) / (rpm * 6) + getattr(self, "feedback_grace_s", 5)
            self._stable_since = None
        if poll:
            self._poll()

    def _poll(self, *, telemetry=True) -> None:
        try:
            self._poll_feedback(telemetry=telemetry)
        except ReplyTimeout:
            if not self._commands.empty():
                return  # A queued stop must run before any further polling.
            raise

    def _poll_feedback(self, *, telemetry=True) -> None:
        status = self._status()
        if not self._commands.empty():
            return  # Yield at the frame boundary to an operator request.
        actual = self._position() - self._offset
        if not self._commands.empty():
            return
        now = time.monotonic()
        speed = 0.0
        if self._previous:
            old_time, old_position = self._previous
            speed = abs(actual - old_position) / max(now - old_time, 0.001) / 6
            if abs(actual - old_position) <= 360 / 65536:
                self._stable_since = self._stable_since or now
            else:
                self._stable_since = None
        self._previous = now, actual
        if self._motion in ("positioning", "continuous"):
            if not status & 1:
                raise RuntimeError("运动期间电机失能")
            # Confirm both trajectory and actual feedback, not just an old arrival bit.
            target = self._state.target_deg
            trajectory = self._position(0x34) - self._offset
            tolerance = max(360 / self.pulses, 360 / 65536)
            if status & 2 and abs(actual - target) <= tolerance and abs(trajectory - target) <= tolerance:
                self._motion = "idle"
                self._deadline = self._move_deadline = None
            elif self._move_deadline is not None and now >= self._move_deadline:
                raise RuntimeError("定位超时")
        if self._motion == "stopping" and self._stable_since and now - self._stable_since >= 0.6:
            self._motion = "idle"
        voltage, current = self._state.bus_voltage_v, self._state.phase_current_a
        # Position/status/trajectory are critical. Electrical telemetry does not
        # delay every command and is skipped when an operator command is queued.
        if telemetry and self._commands.empty() and (voltage is None or now-self._last_telemetry >= 2):
            voltage = int.from_bytes(self._exchange(0x24, length=5)[2:4], "big") / 1000
            if self._commands.empty():
                current = int.from_bytes(self._exchange(0x27, length=5)[2:4], "big") / 1000
                self._last_telemetry = time.monotonic()
        self._state = replace(self._state, connected=True, enabled=bool(status & 1), position_deg=actual,
                              speed_rpm=speed, motion=self._motion if self._commands.empty() else "pending", bus_voltage_v=voltage,
                              phase_current_a=current, remaining_s=max(0, self._deadline-now)
                              if self._deadline else None)

    def _run(self) -> None:
        error = None
        try:
            factory = self.factory
            if factory is None:
                try:
                    from serial import Serial
                except ImportError:
                    raise RuntimeError("请安装串口依赖：python -m pip install -r desktop/requirements.txt") from None
                factory = Serial
            self._serial = factory(self.port, self.baudrate, timeout=0.02, write_timeout=0.3)
            self._serial.reset_input_buffer()
            self._poll()
            while not self._exit.is_set():
                if self._deadline and time.monotonic() >= self._deadline:
                    self._execute("stop", ())
                try:
                    command, args = self._commands.get(timeout=0.05)
                except queue.Empty:
                    self._poll()
                else:
                    self._execute(command, args)
        except Exception as exc:
            error = str(exc)
        finally:
            if self._serial:
                try:
                    if self._motion != "idle":
                        self._exchange(0xFE, b"\x98\x00")
                except Exception as exc:
                    error = error or str(exc)
                finally:
                    self._serial.close()
            self._state = replace(self._state, connected=False, enabled=False, motion="idle",
                                  remaining_s=None, target_deg=None, error=error)
