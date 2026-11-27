"""ST-Link HOTPLUG access to the firmware's existing Watch request mailbox."""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import os
import json
import queue
import re
import shutil
import subprocess
import tempfile
import time

from .controller import Snapshot
from .serial_backend import SerialBackend

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ELF = ROOT / "build/Debug/02_MOTORCONTROL.elf"
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def find_tool(name: str) -> str:
    suffix = ".exe" if os.name == "nt" else ""
    override = os.environ.get("MOTOR_DESK_" + name.upper().replace("-", "_"))
    if override and Path(override).is_file():
        return override
    found = shutil.which(name + suffix)
    if found:
        return found
    roots = [Path("/opt/ST"), Path("/Applications/STMicroelectronics"),
             Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "STMicroelectronics"]
    for root in roots:
        if root.is_dir():
            matches = sorted((path for path in root.glob("**/" + name + suffix)
                              if path.is_file() and os.access(path, os.X_OK)), reverse=True)
            if matches:
                return str(matches[0])
    raise RuntimeError(f"未找到 {name}，请安装 STM32CubeCLT 或将工具加入 PATH")


def run_tool(args: list[str], timeout=15) -> str:
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        result = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=timeout, **options)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"ST-Link 工具运行失败：{exc}") from None
    output = ANSI.sub("", result.stdout + result.stderr)
    if result.returncode or re.search(r"\bError\s*:", output, re.I):
        lines = [line.strip() for line in output.splitlines() if "error" in line.lower()]
        raise RuntimeError("ST-Link：" + ("；".join(lines[-3:]) or output[-400:]))
    return output


@dataclass(frozen=True)
class ProbeInfo:
    device: str
    description: str = "STM32 ST-Link"
    is_usb: bool = True

    @property
    def label(self) -> str:
        return f"{self.description}  ·  {self.device}"


def stlink_probes() -> list[ProbeInfo]:
    output = run_tool([find_tool("STM32_Programmer_CLI"), "-l", "stlink-only"])
    return [ProbeInfo(sn) for sn in re.findall(r"ST-LINK SN\s*:\s*([A-Za-z0-9]+)", output)]


FIELDS = ("request", "address", "arm", "direction", "pulses", "speed_rpm", "acceleration", "mode", "timeout_ms",
          "app.stage", "app.error", "app.active", "app.result", "app.accepted_requests", "app.rejected_requests", "app.last_request_result",
          "zdt.status.enabled", "zdt.status.stalled", "zdt.status.stall_protection", "zdt.status_valid", "zdt.position_valid",
          "zdt.position", "zdt.position_tick", "zdt.voltage_valid", "zdt.current_valid", "zdt.bus_voltage_mv", "zdt.phase_current_ma", "zdt.arrived",
          "zdt.home.valid", "zdt.home.raw", "zdt.home.mode", "zdt.home.power_on", "zdt.home.speed_rpm", "zdt.home.timeout_ms",
          "zdt.health.valid", "zdt.health.raw")


class WatchLink:
    def __init__(self, serial: str, elf: Path):
        if not re.fullmatch(r"[A-Za-z0-9]+", serial):
            raise ValueError("请选择 ST-Link 调试器")
        self.serial, self.elf = serial, Path(elf).resolve()
        if not self.elf.is_file():
            raise RuntimeError("未找到固件 ELF，请先构建或选择已烧录固件对应的 ELF")
        self.cli = find_tool("STM32_Programmer_CLI")
        self.layout: dict[str, tuple[int, int]] = {}
        self.base = self.size = 0
        self._resolve_layout()

    def _resolve_layout(self):
        args = [find_tool("arm-none-eabi-gdb"), "-q", "-nx", "-batch", str(self.elf)]
        symbol = "'main.c'::s_debug"
        expressions = [f"(unsigned long)&{symbol}", f"sizeof({symbol})"]
        for field in FIELDS:
            expressions += [f"(unsigned long)&{symbol}.{field}", f"sizeof({symbol}.{field})"]
        for expression in expressions:
            args += ["-ex", "p/x " + expression]
        output = run_tool(args)
        values = [int(value, 16) for value in re.findall(r"\$\d+ = (0x[0-9a-fA-F]+)", output)]
        if len(values) != len(expressions):
            raise RuntimeError("ELF 缺少 s_debug 调试字段，请使用本工程 Debug 固件")
        self.base, self.size = values[:2]
        if not 0x20000000 <= self.base < self.base + self.size <= 0x20020000:
            raise RuntimeError("ELF 调试邮箱地址不在 F407 SRAM 范围内")
        for index, field in enumerate(FIELDS):
            address, size = values[2+index*2:4+index*2]
            if (size not in (1, 2, 4, 8) and not (field == "zdt.home.raw" and size == 15)) or not self.base <= address <= self.base+self.size-size:
                raise RuntimeError("ELF 调试字段布局无效")
            self.layout[field] = address, size

    def _call(self, *args):
        return run_tool([self.cli, "-c", "port=SWD", "mode=HOTPLUG", "freq=1000", f"sn={self.serial}", *map(str, args)])

    def read_memory(self, address, size):
        with tempfile.TemporaryDirectory(prefix="motor-watch-") as directory:
            path = Path(directory) / "memory.bin"
            self._call("-u", hex(address), str(size), str(path))
            if not path.is_file() or path.stat().st_size != size:
                raise RuntimeError("ST-Link 读取长度不完整")
            return path.read_bytes()

    def verify_firmware(self):
        with tempfile.TemporaryDirectory(prefix="motor-image-") as directory:
            image = Path(directory) / "firmware.bin"
            run_tool([find_tool("arm-none-eabi-objcopy"), "-O", "binary", str(self.elf), str(image)])
            expected = image.read_bytes()
            if not expected or len(expected) > 1024*1024:
                raise RuntimeError("ELF 固件大小无效")
            if self.read_memory(0x08000000, len(expected)) != expected:
                raise RuntimeError("板上固件与所选 ELF 不一致，请选择烧录时使用的 ELF")

    def check_application(self):
        vector = int.from_bytes(self.read_memory(0xE000ED08, 4), "little")
        if vector == 0:
            # SystemInit leaves VTOR at zero unless USER_VECT_TAB_ADDRESS is set.
            # F407 can map main Flash to this boot alias; inspect the mapping.
            boot_vectors = self.read_memory(0, 8)
            flash_vectors = self.read_memory(0x08000000, 8)
            stack = int.from_bytes(boot_vectors[:4], "little")
            reset = int.from_bytes(boot_vectors[4:], "little")
            if (boot_vectors == flash_vectors and 0x20000000 < stack <= 0x20020000
                    and reset & 1 and 0x08000000 <= (reset & ~1) < 0x08100000):
                return
            if 0x1FFF0000 <= (reset & ~1) < 0x20000000:
                raise RuntimeError("MCU 当前处于系统 Bootloader；请检查 BOOT0，使用主 Flash 启动应用后连接")
            raise RuntimeError("MCU 的启动映射与应用 Flash 不一致，请运行到 main 主循环后连接")
        if 0x1FFF0000 <= vector < 0x20000000:
            raise RuntimeError("MCU 当前处于系统 Bootloader；请检查 BOOT0，使用主 Flash 启动应用后连接")
        if not 0x08000000 <= vector < 0x08100000:
            raise RuntimeError(f"MCU 应用向量表地址无效：0x{vector:08X}")

    def read(self):
        data = self.read_memory(self.base, self.size)
        result = {}
        for field, (address, size) in self.layout.items():
            offset = address-self.base
            result[field] = list(data[offset:offset+size]) if field == "zdt.home.raw" else int.from_bytes(data[offset:offset+size], "little", signed=field == "zdt.position")
        return result

    def write_request(self, request, fields):
        if request not in (2, 4, 5, 6, 7, 8, 10, 11, 12, 13, 19, 20):
            raise ValueError("不支持的固件请求")
        writable = {"address", "arm", "direction", "pulses", "speed_rpm", "acceleration", "mode", "timeout_ms"}
        args = []
        for field, value in [*fields.items(), ("request", request)]:
            if field != "request" and field not in writable:
                raise ValueError("调试字段不可写")
            address, size = self.layout[field]
            if size not in (1, 4) or not 0 <= int(value) < 2**(size*8):
                raise ValueError("调试参数超出范围")
            args += [f"-w{size*8}", hex(address), hex(int(value))]
        # Publish request last. CubeProgrammer expects -nv AFTER the write it
        # modifies: the firmware clears request before CLI readback can verify it.
        # Firmware accepted/rejected counters confirm delivery instead.
        self._call(*args, "-nv")



class MotionFeedbackError(RuntimeError):
    """The debug link works, but the final motor feedback needs attention."""


class StLinkBackend(SerialBackend):
    CONFIRM_TIMEOUT_S = 15.0

    def __init__(self, serial: str, address=1, pulses_per_turn=3200, elf=DEFAULT_ELF, *, link_factory=WatchLink):
        super().__init__(serial, address=address, pulses_per_turn=pulses_per_turn)
        self.elf = Path(elf)
        self.link_factory = link_factory
        self.link = None
        self._pending = None
        self._pending_request = None
        self._query_index = 0
        self._last_read = None
        self._verified = False
        self._position_tick = None
        self._completion_started = None
        self._completion_queries = []

    def _request(self, request, fields=None):
        state = self.link.read()
        if request != 10 and state["request"]:
            raise RuntimeError("固件请求邮箱忙，请结束 VS Code 调试中的 Watch 写入")
        if request != 10 and state["app.active"]:
            raise RuntimeError("固件正在执行其他请求")
        self._pending_request = request
        self._pending = (state["app.accepted_requests"], state["app.rejected_requests"])
        self.link.write_request(request, {"address": self.address, **(fields or {})})
        self._request_time = time.monotonic()

    @staticmethod
    def _validate(state):
        if (not 0 <= state["request"] <= 20 or not 0 <= state["app.stage"] <= 20
                or state["app.active"] not in (0, 1)):
            raise RuntimeError("固件调试数据无效，请确认板上运行的是所选 ELF 的 main 主循环")

    def _read(self):
        state = self.link.read()
        self._validate(state)
        if self._pending:
            accepted, rejected = self._pending
            if state["app.rejected_requests"] != rejected:
                raise RuntimeError(f"固件拒绝请求：{state['app.last_request_result']}")
            if state["app.accepted_requests"] != accepted:
                self._pending = None
            elif time.monotonic() - self._request_time > 3:
                if state["request"] == 0:
                    raise RuntimeError(f"请求 {self._pending_request} 已从邮箱清除，但固件未确认接收")
                raise RuntimeError(f"固件尚未取走请求 {self._pending_request}，请确认 MCU 主循环正在运行")
        if state["app.stage"] == 14:  # MOTOR_APP_FAILED
            raise RuntimeError(f"固件操作失败：{state['app.error']}")
        if state["zdt.status.stalled"] or state["zdt.status.stall_protection"]:
            raise RuntimeError("电机堵转或堵转保护")
        self._last_read = state
        return state

    def _wait_done(self, timeout=15):
        deadline = time.monotonic()+timeout
        while True:
            state = self._read()
            if not self._pending and not state["app.active"]:
                return state
            if time.monotonic() > deadline:
                raise RuntimeError("保存开机零点超时，请检查驱动回复")
            self._exit.wait(0.05)

    def _save_startup_origin(self):
        self._motion = "saving_origin"
        self._request(13)
        state = self._wait_done()
        if not state["zdt.health.valid"] or state["zdt.health.raw"] & 0x0F != 3:
            raise RuntimeError("编码器/校准未就绪，或电机正在回零")
        self._request(4)
        first = self._wait_done()["zdt.position"]
        self._request(4)
        state = self._wait_done()
        if not state["zdt.position_valid"] or abs(state["zdt.position"]-first)>max(1, round(65536/self.pulses)):
            raise RuntimeError("电机尚未停稳，未保存开机零点")
        if self._exit.is_set():
            return
        self._request(20, {"arm": 0x484F4D45})
        state = self._wait_done()
        if not (state["zdt.home.valid"] and state["zdt.home.mode"] == 0
                and state["zdt.home.power_on"] == 1 and state["zdt.home.speed_rpm"] == 5):
            raise RuntimeError("开机零点保存后的参数校验失败")
        self._offset = 0
        self._previous = None
        self._state = replace(self._state, origin="固定开机零点", target_deg=None)
        self._request(4)
        self._wait_done()
        self._motion = "idle"

    def _execute(self, command, args):
        if command != "stop":
            deadline = time.monotonic()+5
            while self._pending or (self._last_read and self._last_read["app.active"]):
                self._read()
                if self._exit.is_set() or (command == "move" and self._cancel_move.is_set()):
                    return
                if time.monotonic() > deadline:
                    raise RuntimeError("固件忙，请稍后重试")
                self._exit.wait(0.05)
        if command == "stop":
            self._completion_started = None
            self._completion_queries = []
            self._state = replace(self._state, error=None)
            self._request(10)
            self._motion = "stopping"
            self._deadline = self._move_deadline = None
            self._stable_since = None
        elif command == "enable":
            self._request(11 if args[0] else 12)
            self._motion = "pending"
        elif command == "startup_origin":
            self._save_startup_origin()
        elif command == "origin":
            self._offset += self._state.position_deg
            self._state = replace(self._state, origin="用户坐标", target_deg=None)
            self._previous = None
        elif command == "move":
            angle, rpm, duration = args
            delta = angle-self._state.position_deg
            pulses = int(abs(delta)*self.pulses/360+0.5)
            if not pulses:
                self._motion = "idle"
                return
            if pulses > 0xFFFFFFFF:
                raise ValueError("目标位移超出脉冲范围")
            if self._cancel_move.is_set() or self._exit.is_set():
                return
            timeout = min(120000, max(1000, int((abs(delta)/(rpm*6)+5)*1000)))
            self._completion_started = None
            self._completion_queries = []
            self._motion = "continuous" if duration else "positioning"
            self._request(6, {"arm": 0x454D4D31, "direction": int(delta < 0), "pulses": pulses,
                              "speed_rpm": int(rpm), "acceleration": 0, "mode": 2, "timeout_ms": timeout})
            self._state = replace(self._state, target_deg=self._state.position_deg + (1 if delta >= 0 else -1)*pulses*360/self.pulses)
            self._deadline = time.monotonic()+duration if duration else None
        self._poll()

    def _confirm_motion(self, state, valid, actual, now):
        if self._completion_started is None:
            if state["app.stage"] == 12 and state["zdt.arrived"]:
                self._completion_started = now
                # A fast FD completion can precede the next firmware position poll.
                # Finish two explicit queries before judging the final position.
                self._completion_queries = [2, 4]
            else:
                if now-self._request_time > 3:
                    raise MotionFeedbackError("未收到本次运动完成确认")
                return False
        if self._completion_queries:
            self._request(self._completion_queries.pop(0))
            return False
        tolerance = max(360/self.pulses, 360/65536)
        if valid and abs(actual-self._state.target_deg) <= tolerance:
            self._completion_started = None
            return True
        if now-self._completion_started >= self.CONFIRM_TIMEOUT_S:
            if not valid:
                raise MotionFeedbackError("运动已结束，但最终位置反馈未更新")
            raise MotionFeedbackError(f"位置反馈未到目标：目标 {self._state.target_deg:.3f}°，实际 {actual:.3f}°")
        self._completion_queries = [4, 2]
        self._request(self._completion_queries.pop(0))
        return False

    def _latch_feedback_fault(self, exc):
        message = str(exc)
        try:
            path = ROOT / "desktop/.runtime/motion_errors.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                         "error": message, "target": self._state.target_deg,
                                         "firmware": self._last_read}, ensure_ascii=False)+"\n")
        except OSError:
            pass
        self._request(10)
        self._motion = "fault"
        self._deadline = self._move_deadline = None
        self._completion_started = None
        self._completion_queries = []
        self._state = replace(self._state, connected=True, motion="fault", remaining_s=None, error=message)

    def _poll(self):
        state = self._read()
        now = time.monotonic()
        active = bool(state["app.active"] or self._pending)
        valid = bool(state["zdt.position_valid"] and state["zdt.status_valid"])
        actual = state["zdt.position"]*360/65536-self._offset
        speed = 0.0
        fresh_position = valid and state["zdt.position_tick"] != self._position_tick
        if fresh_position and self._previous:
            previous_time, previous_position = self._previous
            speed = abs(actual-previous_position)/max(now-previous_time, 0.001)/6
            if abs(actual-previous_position) <= 360/65536:
                self._stable_since = self._stable_since or now
            else:
                self._stable_since = None
        if fresh_position:
            self._previous = now, actual
            self._position_tick = state["zdt.position_tick"]
        if not active:
            if self._motion in ("positioning", "continuous"):
                if self._confirm_motion(state, valid, actual, now):
                    self._motion = "idle"
                    self._deadline = None
            elif self._motion == "stopping":
                if fresh_position and self._stable_since and now-self._stable_since >= 0.6:
                    self._motion = "idle"
            elif self._motion != "fault":
                self._motion = "idle"
            # The firmware polls during motion; at rest ask it to refresh each feedback item.
            if self._motion not in ("positioning", "continuous"):
                self._request((2, 4, 7, 8)[self._query_index % 4])
                self._query_index += 1
        display_motion = "confirming" if self._completion_started is not None else self._motion
        self._state = replace(self._state, connected=self._state.connected or valid, enabled=bool(state["zdt.status.enabled"] and state["zdt.status_valid"]),
                              position_deg=actual if valid else self._state.position_deg, speed_rpm=speed,
                              motion=(display_motion if self._commands.empty() else "pending") if self._state.connected or valid else "connecting",
                              remaining_s=max(0, self._deadline-now) if self._deadline else None,
                              bus_voltage_v=state["zdt.bus_voltage_mv"]/1000 if state["zdt.voltage_valid"] else None,
                              phase_current_a=state["zdt.phase_current_ma"]/1000 if state["zdt.current_valid"] else None)

    def _run(self):
        error = None
        try:
            self.link = self.link_factory(self.port, self.elf)
            self.link.verify_firmware()
            if hasattr(self.link, "check_application"):
                self.link.check_application()
            self._verified = True
            state = self.link.read()
            self._validate(state)
            if state["app.active"] or state["request"]:
                raise RuntimeError("固件正在执行请求，请等待完成后连接")
            self._motion = "idle"
            self._completion_started = None
            self._completion_queries = []
            self._position_tick = None
            self._pending = None
            while not self._exit.is_set():
                if self._deadline and time.monotonic() >= self._deadline:
                    self._execute("stop", ())
                try:
                    try:
                        command, args = self._commands.get_nowait()
                    except queue.Empty:
                        self._poll()
                    else:
                        self._execute(command, args)
                except MotionFeedbackError as exc:
                    self._latch_feedback_fault(exc)
                self._exit.wait(0.1)
        except Exception as exc:
            error = str(exc)
        finally:
            if self._verified and self._motion in ("positioning", "continuous", "stopping"):
                try:
                    self.link.write_request(10, {"address": self.address})
                except Exception as exc:
                    error = error or str(exc)
            self._state = replace(self._state, connected=False, enabled=False, motion="idle", target_deg=None,
                                  remaining_s=None, error=error)
