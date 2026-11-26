"""Motor UI domain and a simulation backend. No hardware is opened by this module."""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re
from typing import Protocol


def firmware_default_rpm() -> int:
    source = Path(__file__).resolve().parents[1] / "Core" / "Src" / "main.c"
    match = re.search(r"^\s*#define\s+APP_JOG_RPM\s+(\d+)U?\b", source.read_text(encoding="utf-8"), re.M)
    if not match:
        raise RuntimeError("无法读取固件默认转速 APP_JOG_RPM")
    return int(match.group(1))


def number(value: object, label: str, low: float, high: float) -> float:
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f"{label}请输入有效数字") from None
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"{label}范围为 {low:g} ～ {high:g}")
    return result


@dataclass(frozen=True)
class Snapshot:
    connected: bool
    enabled: bool
    position_deg: float
    target_deg: float | None
    speed_rpm: float
    motion: str
    remaining_s: float | None
    origin: str
    bus_voltage_v: float | None = None
    phase_current_a: float | None = None


class MotorBackend(Protocol):
    """All angles are signed multi-turn degrees. Implementations own I/O and feedback.

    Future hardware adapters must derive angle/pulse mapping from the actual motor,
    expose unconfirmed/stale feedback, and implement a device-side run watchdog.
    No raw Emm forwarding is mixed into the existing motor_app transaction owner.
    """

    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def enable(self, enabled: bool) -> None: ...
    def move_to(self, angle: float, rpm: float) -> None: ...
    def rotate(self, direction: int, rpm: float, duration: float) -> None: ...
    def stop(self) -> None: ...
    def set_origin(self) -> None: ...
    def update(self, seconds: float) -> None: ...
    def snapshot(self) -> Snapshot: ...


class DemoBackend:
    """Deterministic simulator: displayed position is simulated, never motor feedback."""

    def __init__(self) -> None:
        self.connected = False
        self.enabled = False
        self.position = 0.0
        self.target: float | None = None
        self.rpm = 0.0
        self.remaining: float | None = None
        self.direction = 1
        self.origin = "演示初始零点"

    def connect(self) -> None:
        self.connected = True
        self.enabled = False
        self.stop()

    def disconnect(self) -> None:
        self.stop()
        self.enabled = False
        self.connected = False

    def enable(self, enabled: bool) -> None:
        if not self.connected:
            raise ValueError("请先连接演示设备")
        if not enabled:
            self.stop()
        self.enabled = enabled

    def move_to(self, angle: float, rpm: float) -> None:
        self.target = angle
        self.rpm = rpm

    def rotate(self, direction: int, rpm: float, duration: float) -> None:
        self.target = None
        self.direction, self.rpm, self.remaining = direction, rpm, duration

    def stop(self) -> None:
        self.target = None
        self.remaining = None
        self.rpm = 0.0

    def set_origin(self) -> None:
        self.position = 0.0
        self.origin = "用户设置的坐标零点（演示）"

    def update(self, seconds: float) -> None:
        if not self.connected or not self.enabled or self.rpm == 0:
            return
        if self.remaining is not None:
            elapsed = min(seconds, self.remaining)
            self.position += self.direction * self.rpm * 6.0 * elapsed
            self.remaining = max(0.0, self.remaining - elapsed)
            if self.remaining == 0:
                self.stop()
        elif self.target is not None:
            distance = self.target - self.position
            travel = self.rpm * 6.0 * seconds
            if abs(distance) <= travel:
                self.position = self.target
                self.stop()
            else:
                self.position += math.copysign(travel, distance)

    def snapshot(self) -> Snapshot:
        motion = "continuous" if self.remaining is not None else "positioning" if self.target is not None else "idle"
        return Snapshot(self.connected, self.enabled, self.position, self.target,
                        self.rpm if motion != "idle" else 0.0, motion, self.remaining, self.origin)


class Controller:
    MODES = ("position", "continuous")

    def __init__(self, backend: MotorBackend | None = None) -> None:
        self.backend = backend or DemoBackend()
        self.mode = "position"
        self.armed: tuple[int, float, float] | None = None

    def _ready(self) -> Snapshot:
        state = self.backend.snapshot()
        if not state.connected:
            raise ValueError("请先连接演示设备")
        if not state.enabled:
            raise ValueError("请先使能电机")
        if state.motion != "idle":
            raise ValueError("电机正在运行，请先停止；重复指令不会排队")
        return state

    @staticmethod
    def _rpm(value: object) -> float:
        rpm = number(value, "转速 RPM", 1, 3000)
        if not rpm.is_integer():
            raise ValueError("转速 RPM 请输入整数，与当前固件参数一致")
        return rpm

    @staticmethod
    def _continuous_params(direction: int, rpm: object, duration: object) -> tuple[int, float, float]:
        if direction not in (-1, 1):
            raise ValueError("请先选择连续旋转方向")
        return direction, Controller._rpm(rpm), number(duration, "最长运行时间（秒）", 0.1, 60)

    def connect(self) -> None:
        self.armed = None
        self.backend.connect()

    def disconnect(self) -> None:
        self.armed = None
        self.backend.disconnect()

    def enable(self, enabled: bool) -> None:
        self.armed = None
        self.backend.enable(enabled)

    def change_mode(self, mode: str) -> None:
        if mode not in self.MODES:
            raise ValueError("未知控制模式")
        if mode != self.mode:
            self.stop()
            self.mode = mode

    def move_to(self, angle: object, rpm: object) -> None:
        if self.mode != "position":
            raise ValueError("请切换到角度定位模式")
        self._ready()
        target = number(angle, "目标角度（度）", -360000, 360000)
        speed = self._rpm(rpm)
        self.armed = None
        self.backend.move_to(target, speed)

    def jog(self, direction: int, step: object, rpm: object) -> None:
        state = self._ready()
        if direction not in (-1, 1):
            raise ValueError("方向无效")
        degrees = number(step, "单步角度（度）", 0.001, 36000)
        self.move_to(state.position_deg + direction * degrees, rpm)

    def return_zero(self, rpm: object) -> None:
        self.move_to(0.0, rpm)

    def set_origin(self) -> None:
        state = self.backend.snapshot()
        if not state.connected or state.motion != "idle":
            raise ValueError("仅在已连接且电机停止时可以设置零点")
        self.armed = None
        self.backend.set_origin()

    def arm(self, direction: int, rpm: object, duration: object) -> None:
        self.armed = None
        if self.mode != "continuous":
            raise ValueError("请切换到连续旋转模式")
        self._ready()
        self.armed = self._continuous_params(direction, rpm, duration)

    def rotate(self, direction: int, rpm: object, duration: object) -> None:
        armed, self.armed = self.armed, None  # Every attempt consumes the lock.
        if self.mode != "continuous":
            raise ValueError("请切换到连续旋转模式")
        self._ready()
        params = self._continuous_params(direction, rpm, duration)
        if armed != params:
            raise ValueError("请确认方向、转速和时间，然后重新解锁")
        self.backend.rotate(*params)

    def stop(self) -> None:
        self.armed = None
        self.backend.stop()

    def update(self, seconds: float) -> None:
        elapsed = number(seconds, "更新时间", 0, 86400)
        self.backend.update(elapsed)

    def snapshot(self) -> Snapshot:
        return self.backend.snapshot()
