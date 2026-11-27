"""Run with python -m desktop.app. Tk desktop application for macOS and Windows."""
from __future__ import annotations

import math
from pathlib import Path
import queue
import threading
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

if __package__:
    from .controller import Controller, DemoBackend, firmware_default_rpm
    from .serial_backend import SerialBackend, serial_port_details
    from .stlink_backend import StLinkBackend, DEFAULT_ELF, stlink_probes
else:
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from desktop.controller import Controller, DemoBackend, firmware_default_rpm
    from desktop.serial_backend import SerialBackend, serial_port_details
    from desktop.stlink_backend import StLinkBackend, DEFAULT_ELF, stlink_probes


BG = "#F2F5FA"
WHITE = "#FFFFFF"
INK = "#172B4D"
MUTED = "#65748B"
BLUE = "#245CE5"
RED = "#CF3A46"
LINE = "#E4EAF3"


class MotorApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Motor Desk · 电机控制台")
        self.geometry(f"1180x{max(800, min(900, self.winfo_screenheight() - 100))}")
        self.minsize(1060, 800)
        self.configure(bg=BG)
        self.controller = Controller()
        self.default_rpm = firmware_default_rpm()
        self.last_update = time.monotonic()
        self.last_motion = "idle"
        self.position_rpm = tk.StringVar(value=str(self.default_rpm))
        self.continuous_rpm = tk.StringVar(value=str(self.default_rpm))
        self.step = tk.StringVar(value="1")
        self.target = tk.StringVar(value="90")
        self.duration = tk.StringVar(value="10")
        self.direction = tk.IntVar(value=0)
        self.unlock = tk.BooleanVar(value=False)
        self.connection_mode = tk.StringVar(value="模拟连接")
        self.elf_path = tk.StringVar(value=str(DEFAULT_ELF))
        self.device_label = tk.StringVar(value="连接设备")
        self.probe_results = queue.Queue()
        self.probe_scanning = False
        self.port = tk.StringVar()
        self.port_choice = tk.StringVar()
        self.port_hint = tk.StringVar(value="模拟连接不使用串口；连接设备请选择连接方式")
        self.port_labels: dict[str, str] = {}
        self.known_ports: set[str] = set()
        self.next_port_scan = 0.0
        self.baudrate = tk.StringVar(value="115200")
        self.address = tk.StringVar(value="1")
        self.pulses = tk.StringVar(value="3200")
        self.last_error = None
        self._saving_startup_origin = False
        self.font_family = "PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei UI" if sys.platform == "win32" else "sans-serif"
        self.notice = tk.StringVar(value="请选择连接方式")
        self.connection = tk.StringVar(value="未连接")
        self.angle = tk.StringVar(value="—")
        self.info: dict[str, tk.StringVar] = {}
        self.actions: list[ttk.Button] = []
        self._styles()
        self._layout()
        self.port_choice.trace_add("write", self._port_selected)
        for var in (self.continuous_rpm, self.duration, self.direction):
            var.trace_add("write", self._invalidate_lock)
        self.bind("<Escape>", lambda _event: self._stop())
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._log("电机控制台已启动")
        self._refresh()
        self.tick_id = self.after(50, self._tick)

    def _styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        self.option_add("*Font", (self.font_family, 10))
        style.configure("TFrame", background=WHITE)
        style.configure("TLabel", background=WHITE, foreground=INK, font=(self.font_family, 10))
        style.configure("Muted.TLabel", foreground=MUTED, font=(self.font_family, 9))
        style.configure("Section.TLabel", font=(self.font_family, 13, "bold"))
        style.configure("TButton", font=(self.font_family, 10), padding=(12, 9),
                        background="#EAF0FC", foreground=INK, borderwidth=0)
        style.map("TButton", background=[("active", "#DCE7FF"), ("disabled", "#F1F3F7")],
                  foreground=[("disabled", "#9AA7B9")])
        style.configure("Primary.TButton", background=BLUE, foreground=WHITE)
        style.map("Primary.TButton", background=[("disabled", "#D5DFF4"), ("active", "#174AC7")],
                  foreground=[("disabled", "#8799BB")])
        style.configure("Stop.TButton", background=RED, foreground=WHITE, font=(self.font_family, 12, "bold"))
        style.map("Stop.TButton", background=[("active", "#AD2531")])
        style.configure("TEntry", padding=7, fieldbackground="#F8FAFD", bordercolor=LINE)
        style.configure("TNotebook", background=WHITE, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(20, 11), font=(self.font_family, 11))
        style.map("TNotebook.Tab", background=[("selected", "#EAF0FC")], foreground=[("selected", BLUE)])
        style.configure("TRadiobutton", background=WHITE, foreground=INK, padding=8)
        style.configure("TCheckbutton", background=WHITE, foreground=INK, padding=6)

    @staticmethod
    def _label(parent: tk.Widget, text: str, style: str = "TLabel", **kwargs: object) -> ttk.Label:
        return ttk.Label(parent, text=text, style=style, **kwargs)

    def _button(self, parent: tk.Widget, text: str, command, *, primary: bool = False,
                managed: bool = True) -> ttk.Button:
        button = ttk.Button(parent, text=text, command=command,
                            style="Primary.TButton" if primary else "TButton")
        if managed:
            self.actions.append(button)
        return button

    def _layout(self) -> None:
        header = tk.Frame(self, bg="#142747", padx=26, pady=10)
        header.pack(fill="x")
        line = tk.Frame(header, bg="#142747")
        line.pack(fill="x")
        tk.Label(line, text="电机控制台", bg="#142747", fg=WHITE,
                 font=(self.font_family, 20, "bold")).pack(side="left")
        ttk.Button(line, text="■  停止 / Esc", style="Stop.TButton", command=self._stop).pack(side="right")

        content = tk.Frame(self, bg=BG, padx=22, pady=12)
        content.pack(fill="both", expand=True)
        connection = ttk.Frame(content, padding=(16, 10))
        connection.pack(fill="x", pady=(0, 8))
        self._label(connection, "设备连接", "Section.TLabel").pack(side="left", padx=(0, 18))
        self.mode_picker = ttk.Combobox(connection, textvariable=self.connection_mode,
                                        values=("模拟连接", "ST-Link 连接", "串口连接"), state="readonly", width=14)
        self.mode_picker.pack(side="left")
        self.mode_picker.bind("<<ComboboxSelected>>", self._connection_changed)
        ttk.Label(connection, textvariable=self.connection, foreground=BLUE).pack(side="left", padx=16)
        self.connect_button = self._button(connection, "连接", self._connect, primary=True, managed=False)
        self.connect_button.pack(side="right")
        self.enable_button = self._button(connection, "使能电机", self._enable, managed=False)
        self.enable_button.pack(side="right", padx=10)
        self.serial_settings = ttk.Frame(content, padding=(12, 8))
        self.serial_settings.pack(fill="x", pady=(0, 10))
        port_row = ttk.Frame(self.serial_settings)
        port_row.pack(fill="x")
        ttk.Label(port_row, textvariable=self.device_label).pack(side="left", padx=(0, 10))
        self.port_picker = ttk.Combobox(port_row, textvariable=self.port_choice, width=52)
        self.port_picker.pack(side="left", fill="x", expand=True)
        self.scan_button = ttk.Button(port_row, text="刷新设备", command=self._scan_ports)
        self.scan_button.pack(side="left", padx=(10, 0))
        settings_row = ttk.Frame(self.serial_settings)
        settings_row.pack(fill="x", pady=(6, 0))
        self.serial_entries = [self.port_picker]
        self.baud_widgets = []
        self.elf_button = ttk.Button(settings_row, text="选择固件 ELF", command=self._choose_elf)
        for label, variable, width in (("波特率", self.baudrate, 9), ("地址", self.address, 4), ("每圈脉冲", self.pulses, 8)):
            field_label = self._label(settings_row, label)
            field_label.pack(side="left", padx=(0, 5))
            entry = ttk.Entry(settings_row, textvariable=variable, width=width)
            entry.pack(side="left", padx=(0, 12))
            self.serial_entries.append(entry)
            if variable is self.address:
                self.address_label = field_label
            if variable is self.baudrate:
                self.baud_widgets.extend((field_label, entry))
        self.elf_button.pack(side="left", padx=(0, 10))
        self.elf_button.pack_forget()
        ttk.Label(settings_row, textvariable=self.port_hint, style="Muted.TLabel",
                  wraplength=480).pack(side="left", fill="x", expand=True)

        footer = tk.Frame(content, bg=BG)
        footer.pack(side="bottom", fill="x")
        body = tk.Frame(content, bg=BG)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3, minsize=590)
        body.columnconfigure(1, weight=2, minsize=400)
        body.rowconfigure(0, weight=1)
        left = ttk.Frame(body, padding=14)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        self._label(left, "运动控制", "Section.TLabel").pack(anchor="w", pady=(0, 12))
        self.tabs = ttk.Notebook(left)
        self.tabs.pack(fill="both", expand=True)
        self.position_tab = ttk.Frame(self.tabs, padding=(12, 10))
        self.continuous_tab = ttk.Frame(self.tabs, padding=(12, 10))
        self.tabs.add(self.position_tab, text="角度定位")
        self.tabs.add(self.continuous_tab, text="连续旋转")
        self._position_layout()
        self._continuous_layout()
        self.tabs.bind("<<NotebookTabChanged>>", self._mode_changed)

        right = ttk.Frame(body, padding=14)
        right.grid(row=0, column=1, sticky="nsew")
        self._label(right, "电机信息", "Section.TLabel").pack(anchor="w")
        self.dial = tk.Canvas(right, height=80, bg=WHITE, highlightthickness=0)
        self.dial.pack(fill="x", pady=(6, 0))
        ttk.Label(right, textvariable=self.angle, font=(self.font_family, 24, "bold"), foreground=BLUE).pack()
        self._label(right, "累计角度", "Muted.TLabel").pack(pady=(0, 9))
        rows = ttk.Frame(right)
        rows.pack(fill="x")
        for key, label in (("motion", "运行状态"), ("enabled", "电机使能"), ("target", "目标角度"),
                           ("speed", "当前转速"), ("remaining", "剩余运行时间"), ("voltage", "电压 / 电流")):
            row = ttk.Frame(rows)
            row.pack(fill="x", pady=1)
            self._label(row, label, "Muted.TLabel").pack(side="left")
            self.info[key] = tk.StringVar(value="—")
            ttk.Label(row, textvariable=self.info[key]).pack(side="right")
        ttk.Separator(right).pack(fill="x", pady=8)
        self.info["origin"] = tk.StringVar()
        ttk.Label(right, textvariable=self.info["origin"], style="Muted.TLabel", wraplength=340).pack(anchor="w")

        notice = ttk.Frame(footer, padding=(12, 9))
        notice.pack(fill="x", pady=(8, 6))
        self.notice_label = ttk.Label(notice, textvariable=self.notice, foreground=BLUE, wraplength=1040)
        self.notice_label.pack(anchor="w")
        log_header = tk.Frame(footer, bg=BG)
        log_header.pack(fill="x", pady=(0, 5))
        tk.Label(log_header, text="操作日志", bg=BG, fg=MUTED, font=(self.font_family, 9)).pack(side="left")
        log_frame = ttk.Frame(footer)
        log_frame.pack(fill="x")
        self.logs = tk.Text(log_frame, height=2, wrap="word", bg="#EAF0F8", fg=MUTED, relief="flat",
                            padx=10, pady=7, font=(self.font_family, 9), state="disabled")
        self.logs.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_frame, command=self.logs.yview)
        scroll.pack(side="right", fill="y")
        self.logs.configure(yscrollcommand=scroll.set)

    def _field(self, parent: tk.Widget, label: str, variable: tk.StringVar, unit: str) -> ttk.Frame:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(0, 6))
        self._label(row, label, width=12).pack(side="left")
        ttk.Entry(row, textvariable=variable, width=13).pack(side="left", padx=(0, 8))
        self._label(row, unit, "Muted.TLabel").pack(side="left")
        return row

    def _position_layout(self) -> None:
        parent = self.position_tab
        self._field(parent, "单步角度", self.step, "°")
        buttons = ttk.Frame(parent)
        buttons.pack(fill="x", pady=(0, 10))
        self.reverse_button = self._button(buttons, "↶  逆时针一步", lambda: self._jog(1))
        self.reverse_button.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.forward_button = self._button(buttons, "↷  顺时针一步", lambda: self._jog(-1), primary=True)
        self.forward_button.pack(side="left", fill="x", expand=True)
        row = self._field(parent, "目标角度", self.target, "°")
        self.goto_button = self._button(row, "转到指定角度", self._goto)
        self.goto_button.pack(side="right")
        row = self._field(parent, "定位转速", self.position_rpm, "RPM")
        self._button(row, f"默认 {self.default_rpm} RPM", lambda: self._default(self.position_rpm), managed=False).pack(side="right")
        ttk.Separator(parent).pack(fill="x", pady=(3, 8))
        row = ttk.Frame(parent)
        row.pack(fill="x")
        self.zero_button = self._button(row, "复位 · 返回 0°", self._zero)
        self.zero_button.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.origin_button = self._button(row, "保存为开机零点", self._save_startup_origin)
        self.origin_button.pack(side="left", fill="x", expand=True)

    def _continuous_layout(self) -> None:
        parent = self.continuous_tab
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(0, 10))
        self._label(row, "旋转方向", width=12).pack(side="left")
        self.direction_radios = []
        for text, value in (("顺时针", -1), ("逆时针", 1)):
            radio = ttk.Radiobutton(row, text=text, variable=self.direction, value=value)
            radio.pack(side="left", padx=(0, 20))
            self.direction_radios.append(radio)
        row = self._field(parent, "连续转速", self.continuous_rpm, "RPM")
        self._button(row, f"默认 {self.default_rpm} RPM", lambda: self._default(self.continuous_rpm), managed=False).pack(side="right")
        self._field(parent, "最长运行时间", self.duration, "秒 · 0.1 ～ 60")
        ttk.Separator(parent).pack(fill="x", pady=(3, 10))
        self.lock_check = ttk.Checkbutton(parent, text="解锁本次启动", variable=self.unlock, command=self._arm)
        self.lock_check.pack(anchor="w", pady=(0, 8))
        self.lock_text = tk.StringVar(value="启动已锁定")
        ttk.Label(parent, textvariable=self.lock_text, foreground="#9C671C").pack(anchor="w", pady=(0, 12))
        self.run_button = self._button(parent, "启动连续旋转", self._rotate, primary=True)
        self.run_button.pack(fill="x")

    def _log(self, text: str) -> None:
        try:
            log_path = Path(__file__).resolve().parent / ".runtime" / "operations.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {text}\n")
        except OSError:
            pass
        self.logs.configure(state="normal")
        self.logs.insert("end", f"{time.strftime('%H:%M:%S')}  {text}\n")
        if int(self.logs.index("end-1c").split(".")[0]) > 300:
            self.logs.delete("1.0", "2.0")
        self.logs.see("end")
        self.logs.configure(state="disabled")

    def _perform(self, action, success: str) -> bool:
        try:
            # Account for elapsed time BEFORE accepting another motion.
            self._advance()
            action()
        except (ValueError, RuntimeError) as exc:
            self.notice.set(str(exc))
            self.notice_label.configure(foreground=RED)
            self._log(f"操作未执行 · {exc}")
            self._refresh()
            return False
        self.notice.set(success)
        self.notice_label.configure(foreground=BLUE)
        self._log(success)
        self.last_motion = self.controller.snapshot().motion
        self._refresh()
        return True

    def _port_selected(self, *_args) -> None:
        choice = self.port_choice.get().strip()
        self.port.set(self.port_labels.get(choice, choice))

    def _choose_elf(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="选择板上固件对应的 ELF", filetypes=[("ELF 固件", "*.elf")])
        if path:
            self.elf_path.set(path)
            self.port_hint.set("固件：" + path.rsplit("/", 1)[-1])

    def _scan_ports(self) -> None:
        if self.connection_mode.get() == "ST-Link 连接":
            self.next_port_scan = time.monotonic()+10
            if self.probe_scanning:
                return
            self.probe_scanning = True
            self.port_hint.set("正在查找 ST-Link 调试器…")
            def scan():
                try:
                    self.probe_results.put((stlink_probes(), None))
                except Exception as exc:
                    self.probe_results.put(([], str(exc)))
            threading.Thread(target=scan, daemon=True).start()
            return
        self.next_port_scan = time.monotonic() + 2
        try:
            ports = serial_port_details()
        except (RuntimeError, OSError) as exc:
            self.port_hint.set(str(exc))
            self.port_picker.configure(values=())
            return
        self._apply_devices(ports)

    def _apply_devices(self, ports) -> None:
        current = self.port.get()
        devices = {port.device for port in ports}
        if current in self.known_ports and current not in devices:
            current = ""
        self.known_ports = devices
        self.port_labels = {port.label: port.device for port in ports}
        self.port_picker.configure(values=list(self.port_labels))
        usb_ports = [port for port in ports if port.is_usb]
        if not current and len(usb_ports) == 1:
            current = usb_ports[0].device
        selected = next((port.label for port in ports if port.device == current), current)
        if self.port_choice.get() != selected:
            self.port_choice.set(selected)
        if self.connection_mode.get() == "ST-Link 连接":
            self.port_hint.set(f"发现 {len(ports)} 个 ST-Link；固件：" + self.elf_path.get().replace("\\", "/").rsplit("/", 1)[-1] if ports else "未发现 ST-Link；请连接调试器并结束 VS Code 调试会话")
        elif not usb_ports:
            self.port_hint.set("未发现 USB 串口；插入 USB-RS485 后刷新")
        elif len(usb_ports) == 1:
            self.port_hint.set("已发现 1 个 USB 串口" if current else "请选择连接串口")
        else:
            self.port_hint.set(f"发现 {len(usb_ports)} 个 USB 串口，请按设备名称选择")

    def _connection_changed(self, _event=None) -> None:
        self.port_choice.set("")
        self.known_ports.clear()
        self.port_labels.clear()
        self.port_picker.configure(values=())
        stlink = self.connection_mode.get() == "ST-Link 连接"
        self.device_label.set("ST-Link 调试器" if stlink else "连接串口")
        for widget in self.baud_widgets:
            widget.pack_forget()
        self.elf_button.pack_forget()
        if stlink:
            self.elf_button.pack(side="left", before=self.address_label, padx=(0, 10))
        else:
            # Restore baud controls before the address controls.
            for widget in self.baud_widgets:
                widget.pack(side="left", before=self.address_label, padx=(0, 5))
        if self.connection_mode.get() != "模拟连接":
            self._scan_ports()
        else:
            self.port_hint.set("模拟连接不使用串口；连接设备请选择连接方式")
        self._refresh()

    def _connect(self) -> None:
        if self.controller.snapshot().connected:
            self._perform(self.controller.disconnect, "已断开" if isinstance(self.controller.backend, DemoBackend) else "正在断开")
        else:
            def connect():
                if self.connection_mode.get() == "ST-Link 连接":
                    backend = StLinkBackend(self.port.get(), self.address.get(), self.pulses.get(), self.elf_path.get())
                    self.controller.select_backend(backend)
                elif self.connection_mode.get() == "串口连接":
                    backend = SerialBackend(self.port.get(), self.baudrate.get(), self.address.get(), self.pulses.get())
                    self.controller.select_backend(backend)
                elif not isinstance(self.controller.backend, DemoBackend):
                    self.controller.select_backend(DemoBackend())
                self.last_error = None
                self.controller.connect()
            self._perform(connect, "模拟设备已连接" if self.connection_mode.get() == "模拟连接" else "正在连接设备")

    def _enable(self) -> None:
        enabled = not self.controller.snapshot().enabled
        self._perform(lambda: self.controller.enable(enabled), "使能请求已提交" if enabled else "失能请求已提交")

    def _jog(self, direction: int) -> None:
        self._perform(lambda: self.controller.jog(direction, self.step.get(), self.position_rpm.get()),
                      f"{'顺时针' if direction == -1 else '逆时针'}一步 · {self.step.get()}° · {self.position_rpm.get()} RPM")

    def _goto(self) -> None:
        self._perform(lambda: self.controller.move_to(self.target.get(), self.position_rpm.get()),
                      f"定位至 {self.target.get()}° · {self.position_rpm.get()} RPM")

    def _zero(self) -> None:
        self._perform(lambda: self.controller.return_zero(self.position_rpm.get()), "返回 0°")

    def _save_startup_origin(self) -> None:
        self._saving_startup_origin = True
        if not self._perform(self.controller.save_startup_origin, "正在保存开机零点"):
            self._saving_startup_origin = False

    def _origin(self) -> None:
        if messagebox.askyesno("设置坐标零点", "将当前位置设为 0°？", parent=self):
            self._perform(self.controller.set_origin, "零点设置已提交")

    def _default(self, variable: tk.StringVar) -> None:
        variable.set(str(self.default_rpm))
        self._log(f"转速输入已恢复为固件默认值 {self.default_rpm} RPM（下次启动生效）")

    def _invalidate_lock(self, *_args: object) -> None:
        self.controller.armed = None
        self.unlock.set(False)
        self._refresh()

    def _arm(self) -> None:
        if not self.unlock.get():
            self._invalidate_lock()
            return
        self._perform(lambda: self.controller.arm(self.direction.get(), self.continuous_rpm.get(), self.duration.get()),
                      "已解锁")

    def _rotate(self) -> None:
        self._perform(lambda: self.controller.rotate(self.direction.get(), self.continuous_rpm.get(), self.duration.get()),
                      f"连续旋转已启动 · {self.continuous_rpm.get()} RPM · 最长 {self.duration.get()} 秒")

    def _stop(self) -> None:
        self._perform(self.controller.stop, "停止请求已提交")

    def _mode_changed(self, _event=None) -> None:
        mode = "position" if self.tabs.index(self.tabs.select()) == 0 else "continuous"
        if mode != self.controller.mode:
            self._perform(lambda: self.controller.change_mode(mode),
                          f"已切换到{'角度定位' if mode == 'position' else '连续旋转'}")

    def _draw_dial(self, angle: float | None) -> None:
        canvas = self.dial
        canvas.delete("all")
        cx, cy, radius = max(canvas.winfo_width(), 300) / 2, 40, 30
        canvas.create_oval(cx-radius, cy-radius, cx+radius, cy+radius, outline=LINE, width=10)
        for i in range(12):
            radians = i * math.pi / 6 - math.pi / 2
            canvas.create_line(cx+20*math.cos(radians), cy+20*math.sin(radians),
                               cx+26*math.cos(radians), cy+26*math.sin(radians), fill="#A6B6CE", width=2)
        radians = math.radians(-(angle or 0) % 360) - math.pi / 2
        canvas.create_line(cx, cy, cx+19*math.cos(radians), cy+19*math.sin(radians),
                           fill=BLUE if angle is not None else "#B6C3D6", width=4, capstyle="round")
        canvas.create_oval(cx-5, cy-5, cx+5, cy+5, fill=BLUE if angle is not None else "#B6C3D6", outline="")

    def _refresh(self) -> None:
        state = self.controller.snapshot()
        ready = state.connected and state.enabled and state.motion == "idle"
        for button in self.actions:
            button.configure(state="normal" if ready else "disabled")
        self.origin_button.configure(state="normal" if state.connected and state.motion == "idle" else "disabled")
        self.enable_button.configure(state="normal" if state.connected and state.motion == "idle" else "disabled", text="失能电机" if state.enabled else "使能电机")
        busy_connection = state.motion in ("connecting", "closing")
        self.connect_button.configure(text="断开" if state.connected else "连接", state="disabled" if busy_connection else "normal")
        locked = state.connected or busy_connection
        self.mode_picker.configure(state="disabled" if locked else "readonly")
        serial_locked = locked or self.connection_mode.get() == "模拟连接"
        for entry in self.serial_entries:
            entry.configure(state="disabled" if serial_locked else "normal")
        self.scan_button.configure(state="disabled" if serial_locked or self.probe_scanning else "normal")
        self.elf_button.configure(state="disabled" if locked else "normal")
        self.connection.set("正在连接" if state.motion == "connecting" else "正在断开" if state.motion == "closing" else "已连接" if state.connected else "未连接")
        self.angle.set(f"{state.position_deg:+.3f}°" if state.connected else "—")
        motion_text = {"idle": "待机", "positioning": "角度定位中", "continuous": "连续旋转中", "confirming": "确认到位中", "fault": "位置反馈异常", "saving_origin": "保存开机零点中", "pending": "指令处理中", "stopping": "正在停止", "connecting": "正在连接", "closing": "正在断开"}[state.motion]
        self.info["motion"].set(motion_text if state.connected else "离线")
        self.info["enabled"].set(("已使能" if state.enabled else "未使能") if state.connected else "—")
        self.info["target"].set(f"{state.target_deg:+.3f}°" if state.target_deg is not None else "—")
        self.info["speed"].set(f"{state.speed_rpm:g} RPM" if state.connected else "—")
        self.info["remaining"].set(f"{state.remaining_s:.1f} s" if state.remaining_s is not None else "—")
        voltage = f"{state.bus_voltage_v:.2f} V" if state.connected and state.bus_voltage_v is not None else "—"
        current = f"{state.phase_current_a:.3f} A" if state.connected and state.phase_current_a is not None else "—"
        self.info["voltage"].set(f"{voltage} / {current}")
        self.info["origin"].set("零点：" + (state.origin if state.connected else "未连接"))
        armed = self.controller.armed is not None
        self.unlock.set(armed)
        self.lock_text.set("本次启动已解锁" if armed else "启动已锁定")
        self.lock_check.configure(state="normal" if ready else "disabled")
        for radio in self.direction_radios:
            radio.configure(state="normal" if state.motion == "idle" else "disabled")
        self.run_button.configure(state="normal" if ready and armed and self.controller.mode == "continuous" else "disabled")
        self._draw_dial(state.position_deg if state.connected else None)

    def _advance(self) -> None:
        now = time.monotonic()
        self.controller.update(max(0, now - self.last_update))
        self.last_update = now
        motion = self.controller.snapshot().motion
        state = self.controller.snapshot()
        if self._saving_startup_origin and state.error:
            self._saving_startup_origin = False
        if self._saving_startup_origin and motion == "idle" and state.origin in ("固定开机零点", "模拟开机零点"):
            self._saving_startup_origin = False
            self.notice.set("开机零点已保存")
            self._log("开机零点已保存")
        if self.last_motion == "connecting" and state.connected:
            self.notice.set("设备已连接")
            self.notice_label.configure(foreground=BLUE)
            self._log("设备已连接")
        if state.error and state.error != self.last_error:
            self.last_error = state.error
            self.notice.set(state.error)
            self.notice_label.configure(foreground=RED)
            self._log(state.error)
        if self.last_motion in ("positioning", "continuous", "confirming", "stopping") and motion == "idle" and state.connected and not state.error:
            message = "已到达目标" if self.last_motion in ("positioning", "confirming") else "运行已结束"
            self.notice.set(message)
            self.notice_label.configure(foreground=BLUE)
            self._log(message)
        self.last_motion = motion

    def _tick(self) -> None:
        state = self.controller.snapshot()
        try:
            ports, error = self.probe_results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.probe_scanning = False
            if self.connection_mode.get() == "ST-Link 连接" and not state.connected:
                if error:
                    self.port_hint.set(error)
                else:
                    self._apply_devices(ports)
        if (self.connection_mode.get() != "模拟连接" and not state.connected
                and state.motion == "idle" and time.monotonic() >= self.next_port_scan):
            self._scan_ports()
        self._advance()
        self._refresh()
        self.tick_id = self.after(50, self._tick)

    def _close(self) -> None:
        self.after_cancel(self.tick_id)
        self.controller.disconnect()
        self._finish_close()

    def _finish_close(self) -> None:
        backend = self.controller.backend
        if isinstance(backend, SerialBackend) and backend._thread and backend._thread.is_alive():
            self.after(50, self._finish_close)
        else:
            self.destroy()


def main(*, initial_mode="模拟连接", elf=None, probe=None) -> None:
    # Avoid blurry text on Windows; use Tk's normal scaling for the chosen DPI.
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    app = MotorApp()
    if elf is not None:
        app.elf_path.set(str(elf))
    if initial_mode != "模拟连接":
        app.connection_mode.set(initial_mode)
        app._connection_changed()
        if probe:
            app.port_choice.set(probe)
    app.mainloop()


if __name__ == "__main__":
    main()
