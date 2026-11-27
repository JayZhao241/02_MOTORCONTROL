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
    from .controller import Controller, DemoBackend, firmware_default_rpm, number
    from .serial_backend import SerialBackend, serial_port_details
    from .stlink_backend import StLinkBackend, DEFAULT_ELF, stlink_probes
else:
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from desktop.controller import Controller, DemoBackend, firmware_default_rpm, number
    from desktop.serial_backend import SerialBackend, serial_port_details
    from desktop.stlink_backend import StLinkBackend, DEFAULT_ELF, stlink_probes


if __package__:
    from .dual_backend import DualDemoBackend, DualSerialBackend
    from .pose_control import PoseControl
    from .bridge_transport import BridgeTransport
else:
    from desktop.dual_backend import DualDemoBackend, DualSerialBackend
    from desktop.pose_control import PoseControl
    from desktop.bridge_transport import BridgeTransport


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
        self.controller = Controller(DualDemoBackend())
        self.pose = PoseControl(self.controller.backend)
        self.motor_choice = tk.StringVar(value="电机1 · 左轴 · ID 1")
        self.alpha = tk.StringVar(value="0")
        self.beta = tk.StringVar(value="0")
        self.pose_rpm = tk.StringVar(value="1")
        self.left_sign = tk.StringVar(value="+1")
        self.right_sign = tk.StringVar(value="+1")
        self.sweep_axis = tk.StringVar(value="扇扫 α")
        self.amplitude = tk.StringVar(value="1")
        self.sweep_rpm = tk.StringVar(value="1")
        self.use_limits = tk.BooleanVar(value=False)
        self.alpha_limit = tk.StringVar()
        self.beta_limit = tk.StringVar()
        self.limit_status = tk.StringVar(value="未启用软件限位；机械范围待实验测量")
        self.fixed_label = tk.StringVar(value="固定俯仰 β")
        self.pose_preview = tk.StringVar()
        self.sweep_preview = tk.StringVar()
        self.status_title = tk.StringVar(value="单电机反馈 · ID 1")
        self.dual_status = tk.StringVar()
        self.fixed = tk.StringVar(value="0")
        self.cycles = tk.StringVar(value="1")
        self.dual_info = tk.StringVar()
        self.default_rpm = firmware_default_rpm()
        self.last_update = time.monotonic()
        self.last_motion = "idle"
        self.position_rpm = tk.StringVar(value=str(self.default_rpm))
        self.continuous_rpm = tk.StringVar(value=str(self.default_rpm))
        self.step = tk.StringVar(value="1")
        self.target = tk.StringVar(value="0")
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
        self.angle_caption = tk.StringVar(value="单圈角度 · 累计角度 —")
        self.info: dict[str, tk.StringVar] = {}
        self.actions: list[ttk.Button] = []
        self.navigation = []
        self.motion_inputs = []
        self.activity_text = tk.StringVar(value="连接设备，开始控制")
        self.elapsed_text = tk.StringVar()
        self.workflow_text = tk.StringVar()
        self.link_text = tk.StringVar()
        self._busy_since = None
        self._scanning = False
        self._scan_total = 0
        self._move_starts = None
        self._progress_mode = None
        self._styles()
        self._layout()
        for variable in (self.alpha, self.beta, self.sweep_axis, self.amplitude, self.fixed, self.cycles):
            variable.trace_add("write", self._parameter_hints)
        self._parameter_hints()
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
        style.configure("TButton", font=(self.font_family, 10), padding=(10, 7),
                        background="#EAF0FC", foreground=INK, borderwidth=0)
        style.map("TButton", background=[("active", "#DCE7FF"), ("disabled", "#F1F3F7")],
                  foreground=[("disabled", "#9AA7B9")])
        style.configure("Primary.TButton", background=BLUE, foreground=WHITE)
        style.map("Primary.TButton", background=[("disabled", "#D5DFF4"), ("active", "#174AC7")],
                  foreground=[("disabled", "#8799BB")])
        style.configure("Stop.TButton", background=RED, foreground=WHITE, font=(self.font_family, 12, "bold"))
        style.map("Stop.TButton", background=[("active", "#AD2531")])
        style.configure("TEntry", padding=7, fieldbackground="#F8FAFD", bordercolor=LINE)
        style.configure("Input.TEntry", padding=7, fieldbackground="#FFFFFF", bordercolor="#7C9DE8",
                        font=(self.font_family, 12))
        style.configure("TLabelframe", background=WHITE, bordercolor=LINE)
        style.configure("TLabelframe.Label", background=WHITE, foreground=BLUE, font=(self.font_family, 10, "bold"))
        style.configure("TNotebook", background=WHITE, borderwidth=0)
        style.layout("Flat.TNotebook", [("Notebook.client", {"sticky": "nswe"})])
        style.layout("Flat.TNotebook.Tab", [])
        for name, large, selected in (("Nav.TButton",False,False),("NavSelected.TButton",False,True),
                                       ("NavLarge.TButton",True,False),("NavSelectedLarge.TButton",True,True)):
            style.configure(name, background=BLUE if selected else "#E9EFF8", foreground=WHITE if selected else MUTED,
                            padding=(10, 6 if large else 4), font=(self.font_family, 11 if large else 10, "bold"), borderwidth=0)
            style.map(name, background=[("disabled", "#EDF1F7"), ("active", "#174AC7" if selected else "#DCE6F4")],
                      foreground=[("disabled", "#9AA7B9")])
        style.configure("TCombobox", padding=5, fieldbackground=WHITE, background="#EDF2F8", bordercolor=LINE, arrowcolor=MUTED)
        style.map("TCombobox", fieldbackground=[("readonly", WHITE), ("disabled", "#F2F4F8")], selectbackground=[("readonly", WHITE)], selectforeground=[("readonly", INK)])
        style.configure("Activity.Horizontal.TProgressbar", background=BLUE, troughcolor="#E8EDF5", borderwidth=0, thickness=4)
        style.configure("TNotebook.Tab", padding=(12, 5), font=(self.font_family, 10))
        style.configure("Workspace.TNotebook.Tab", padding=(14, 7), font=(self.font_family, 12, "bold"))
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
        header = tk.Frame(self, bg="#142747", padx=26, pady=8)
        header.pack(fill="x")
        line = tk.Frame(header, bg="#142747")
        line.pack(fill="x")
        tk.Label(line, text="Motor Desk", bg="#142747", fg=WHITE,
                 font=(self.font_family, 20, "bold")).pack(side="left")
        tk.Label(line, text="双轴运动控制台", bg="#142747", fg="#B5C6E1", font=(self.font_family, 11)).pack(side="left", padx=16)
        ttk.Button(line, text="■  全部停止 / Esc", style="Stop.TButton", command=self._stop).pack(side="right")

        content = tk.Frame(self, bg=BG, padx=22, pady=8)
        content.pack(fill="both", expand=True)
        connection = ttk.Frame(content, padding=(16, 7))
        connection.pack(fill="x", pady=(0, 8))
        self._label(connection, "设备连接", "Section.TLabel").pack(side="left", padx=(0, 18))
        self.mode_picker = ttk.Combobox(connection, textvariable=self.connection_mode,
                                        values=("模拟连接", "ST-Link 连接", "串口连接"), state="readonly", width=14)
        self.mode_picker.pack(side="left")
        self.mode_picker.bind("<<ComboboxSelected>>", self._connection_changed)
        ttk.Label(connection, textvariable=self.connection, foreground=BLUE).pack(side="left", padx=16)
        self.connect_button = self._button(connection, "连接", self._connect, primary=True, managed=False)
        self.connect_button.pack(side="right")
        self.serial_settings = ttk.Frame(content, padding=(12, 6))
        self.serial_settings.pack(fill="x", pady=(0, 6))
        port_row = ttk.Frame(self.serial_settings)
        port_row.pack(fill="x")
        ttk.Label(port_row, textvariable=self.device_label).pack(side="left", padx=(0, 10))
        self.port_picker = ttk.Combobox(port_row, textvariable=self.port_choice, width=52)
        self.port_picker.pack(side="left", fill="x", expand=True)
        self.scan_button = ttk.Button(port_row, text="刷新设备", command=self._scan_ports)
        self.scan_button.pack(side="left", padx=(10, 0))
        settings_row = ttk.Frame(self.serial_settings)
        self.settings_row = settings_row
        settings_row.pack(fill="x", pady=(6, 0))
        self.serial_entries = [self.port_picker]
        self.baud_widgets = []
        self.elf_button = ttk.Button(settings_row, text="选择固件 ELF", command=self._choose_elf)
        for label, variable, width in (("波特率", self.baudrate, 9), ("每圈脉冲", self.pulses, 8)):
            field_label = self._label(settings_row, label)
            field_label.pack(side="left", padx=(0, 5))
            entry = ttk.Entry(settings_row, textvariable=variable, width=width)
            entry.pack(side="left", padx=(0, 12))
            self.serial_entries.append(entry)
            if variable is self.pulses:
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
        left = ttk.Frame(body, padding=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        self.workspaces = ttk.Notebook(left, style="Flat.TNotebook")
        self._navigation(left, self.workspaces, ("单电机控制", "扇扫与俯仰"), prominent=True)
        self.workspaces.pack(fill="both", expand=True)
        self.single_panel = ttk.Frame(self.workspaces, padding=(8, 8))
        self.pose_tab = ttk.Frame(self.workspaces, padding=(8, 8))
        self.workspaces.add(self.single_panel, text="单电机控制")
        self.workspaces.add(self.pose_tab, text="双电机 · 扇扫 / 俯仰")
        row = ttk.Frame(self.single_panel)
        row.pack(fill="x", pady=(0, 8))
        ttk.Label(row, text="操作电机").pack(side="left", padx=(0, 8))
        self.motor_picker = ttk.Combobox(row, textvariable=self.motor_choice, state="readonly",
            values=("电机1 · 左轴 · ID 1", "电机2 · 右轴 · ID 2"), width=22)
        self.motor_picker.pack(side="left")
        self.motor_picker.bind("<<ComboboxSelected>>", self._motor_changed)
        self.enable_button = self._button(row, "使能所选电机", self._enable, managed=False)
        self.enable_button.pack(side="right")
        self.tabs = ttk.Notebook(self.single_panel, style="Flat.TNotebook")
        self._navigation(self.single_panel, self.tabs, ("角度定位 / 点动", "连续旋转"))
        self.tabs.pack(fill="both", expand=True)
        self.position_tab = ttk.Frame(self.tabs, padding=(8, 8))
        self.continuous_tab = ttk.Frame(self.tabs, padding=(8, 8))
        self.tabs.add(self.position_tab, text="角度定位 / 点动")
        self.tabs.add(self.continuous_tab, text="连续旋转")
        self._position_layout()
        self._continuous_layout()
        self._pose_layout()
        self.tabs.bind("<<NotebookTabChanged>>", self._mode_changed)
        self.workspaces.bind("<<NotebookTabChanged>>", self._workspace_changed)

        right = ttk.Frame(body, padding=14)
        right.grid(row=0, column=1, sticky="nsew")
        ttk.Label(right, textvariable=self.status_title, style="Section.TLabel").pack(anchor="w")
        ttk.Label(right, textvariable=self.dual_info, wraplength=350, style="Muted.TLabel").pack(fill="x", pady=(6, 0))
        self.single_feedback = ttk.Frame(right)
        self.single_feedback.pack(fill="both", expand=True)
        detail = self.single_feedback
        self.dual_feedback = ttk.Frame(right)
        self.motor_cards = []
        for title in ("左轴 · ID 1", "右轴 · ID 2"):
            card = ttk.LabelFrame(self.dual_feedback, text=title, padding=(12, 8))
            card.pack(fill="x", pady=(8, 0))
            position, status, target = tk.StringVar(value="—"), tk.StringVar(value="离线"), tk.StringVar(value="目标 —")
            row = ttk.Frame(card)
            row.pack(fill="x")
            ttk.Label(row, textvariable=position, font=(self.font_family, 26, "bold"), foreground=BLUE).pack(side="left")
            ttk.Label(row, textvariable=status, style="Muted.TLabel").pack(side="right")
            ttk.Label(card, textvariable=target, style="Muted.TLabel").pack(anchor="w")
            self.motor_cards.append((position, status, target))
        ttk.Label(self.dual_feedback, textvariable=self.limit_status, style="Muted.TLabel", wraplength=340).pack(fill="x", pady=8)
        guide = ttk.LabelFrame(right, text="下一步", padding=(10, 7))
        guide.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Label(guide, textvariable=self.workflow_text, wraplength=330).pack(anchor="w")
        ttk.Label(guide, textvariable=self.link_text, style="Muted.TLabel", wraplength=330).pack(anchor="w", pady=(4, 0))
        self.dial = tk.Canvas(detail, height=80, bg=WHITE, highlightthickness=0)
        self.dial.pack(fill="x", pady=(6, 0))
        ttk.Label(detail, textvariable=self.angle, font=(self.font_family, 24, "bold"), foreground=BLUE).pack()
        ttk.Label(detail, textvariable=self.angle_caption, style="Muted.TLabel").pack(pady=(0, 9))
        rows = ttk.Frame(detail)
        rows.pack(fill="x")
        for key, label in (("motion", "运行状态"), ("enabled", "电机使能"), ("target", "目标累计角度"),
                           ("speed", "当前转速"), ("remaining", "剩余运行时间"), ("voltage", "电压 / 电流")):
            row = ttk.Frame(rows)
            row.pack(fill="x", pady=1)
            self._label(row, label, "Muted.TLabel").pack(side="left")
            self.info[key] = tk.StringVar(value="—")
            ttk.Label(row, textvariable=self.info[key]).pack(side="right")
        ttk.Separator(detail).pack(fill="x", pady=8)
        self.info["origin"] = tk.StringVar()
        ttk.Label(detail, textvariable=self.info["origin"], style="Muted.TLabel", wraplength=340).pack(anchor="w")

        notice = ttk.Frame(footer, padding=(12, 6))
        notice.pack(fill="x", pady=(5, 4))
        line = ttk.Frame(notice)
        line.pack(fill="x")
        ttk.Label(line, textvariable=self.activity_text, font=(self.font_family, 11, "bold")).pack(side="left")
        ttk.Label(line, textvariable=self.elapsed_text, style="Muted.TLabel").pack(side="right")
        progress_track = ttk.Frame(notice, height=5)
        progress_track.pack(fill="x", pady=(5, 4))
        progress_track.pack_propagate(False)
        self.progress = ttk.Progressbar(progress_track, style="Activity.Horizontal.TProgressbar", mode="indeterminate", maximum=100)
        self.progress.pack(fill="both", expand=True)
        self.notice_label = ttk.Label(notice, textvariable=self.notice, foreground=BLUE, wraplength=1040)
        self.notice_label.pack(anchor="w")
        log_frame = ttk.Frame(footer)
        log_frame.pack(fill="x")
        ttk.Label(log_frame, text="操作日志", style="Muted.TLabel").pack(side="left", padx=8)
        self.logs = tk.Text(log_frame, height=1, wrap="word", bg="#EAF0F8", fg=MUTED, relief="flat",
                            padx=10, pady=7, font=(self.font_family, 9), state="disabled")
        self.logs.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_frame, command=self.logs.yview)
        scroll.pack(side="right", fill="y")
        self.logs.configure(yscrollcommand=scroll.set)

    def _navigation(self, parent, notebook, labels, *, prominent=False):
        row = tk.Frame(parent, bg="#E9EFF8", padx=3, pady=3)
        row.pack(fill="x", pady=(0, 6 if prominent else 4))
        buttons = []
        for index, label in enumerate(labels):
            def select(i=index):
                notebook.select(i)
                self._refresh()
            button = ttk.Button(row, text=label, command=select, cursor="hand2",
                                style="NavLarge.TButton" if prominent else "Nav.TButton")
            button.pack(side="left", fill="x", expand=True, padx=1)
            buttons.append(button)
        self.navigation.append((notebook, buttons, prominent))

    def _activity(self, states, busy, dual):
        now = time.monotonic()
        connected = all(s.connected for s in states)
        error = next((s.error for s in states if s.error), None)
        if busy and not error:
            if self._busy_since is None:
                self._busy_since = now
                self._move_starts = [s.position_deg for s in states]
            self.elapsed_text.set(f"已用 {now-self._busy_since:.1f} 秒")
            phases = {s.motion for s in states}
            if phases & {"connecting", "pending", "closing"}:
                headline = getattr(self.controller.backend,"activity","正在处理指令")
            elif "stopping" in phases:
                headline = "正在停止 · 等待两轴停稳"
            elif self._scanning:
                headline = f"往复扫描中 · 第 {max(1,self._scan_total-len(self.pose.sequence))} / {self._scan_total} 段"
            elif self.pose.sequence:
                headline = f"双轴定位中 · 剩余 {len(self.pose.sequence)} 段"
            else:
                headline = "运动执行中 · 正在读取位置反馈"
            self.activity_text.set(headline)
            mode = "indeterminate"
            progress = []
            if not self._scanning and not self.pose.sequence and phases <= {"positioning", "continuous", "idle"}:
                for initial, state in zip(self._move_starts, states):
                    if state.target_deg is not None and abs(state.target_deg-initial) > 0.001:
                        progress.append(max(0,min(99,100*abs(state.position_deg-initial)/abs(state.target_deg-initial))))
                if progress:
                    mode = "determinate"
            if mode != self._progress_mode:
                self.progress.stop()
                self.progress.configure(mode=mode)
                if mode == "indeterminate":
                    self.progress.start(15)
                self._progress_mode = mode
            if mode == "determinate":
                self.progress.configure(value=min(progress))
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
            self._progress_mode = "determinate"
            self.activity_text.set("操作中断 · 请查看提示" if error else "设备已就绪" if connected else "连接设备，开始控制")
            if self._busy_since is not None:
                self.elapsed_text.set(f"本次用时 {now-self._busy_since:.1f} 秒")
            elif not connected:
                self.elapsed_text.set("")
            self._busy_since = None
            self._move_starts = None
        if not connected:
            guide = "选择连接方式和设备，然后点击“连接”。"
        elif busy:
            guide = "当前操作正在执行，可随时点击“全部停止”或按 Esc。"
        elif dual and not all(s.enabled for s in states):
            guide = "先使能两台电机，再设置目标或扫描参数。"
        elif dual and self.pose.center is None:
            guide = "进入“标定与范围”，将当前机械中心记录为零点。"
        elif dual:
            guide = "修改目标或扫描参数，点击蓝色按钮执行。"
        elif not self.controller.snapshot().enabled:
            guide = "使能所选电机后，即可点动、定位或连续旋转。"
        else:
            guide = f"当前只控制 ID {self.controller.backend.selected+1}；修改参数后点击执行。"
        self.workflow_text.set(guide)
        backend = self.controller.backend
        transport = getattr(backend,"transport_name","本地模拟")
        latency = getattr(getattr(backend,"_serial",None),"last_exchange_ms",None)
        self.link_text.set(f"{transport} · 最近通信 {latency:.0f} ms" if latency is not None else transport)

    def _field(self, parent: tk.Widget, label: str, variable: tk.StringVar, unit: str) -> ttk.Frame:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(0, 6))
        self._label(row, label, width=12).pack(side="left")
        entry = ttk.Entry(row, textvariable=variable, width=11, style="Input.TEntry")
        entry.pack(side="left", padx=(0, 8))
        self.motion_inputs.append(entry)
        self._label(row, unit, "Muted.TLabel").pack(side="left")
        return row

    def _position_layout(self) -> None:
        parent = self.position_tab
        row = self._field(parent, "运动转速", self.position_rpm, "RPM · 本页所有定位动作")
        self._button(row, f"默认 {self.default_rpm}", lambda: self._default(self.position_rpm), managed=False).pack(side="right")
        step_box = ttk.LabelFrame(parent, text="点动 · 按设定步长转动", padding=(8, 6))
        step_box.pack(fill="x", pady=(0, 8))
        buttons = ttk.Frame(step_box)
        buttons.pack(fill="x")
        ttk.Label(buttons, text="步长 °").pack(side="left", padx=(0, 6))
        entry = ttk.Entry(buttons, textvariable=self.step, width=7, style="Input.TEntry")
        entry.pack(side="left", padx=(0, 8))
        self.motion_inputs.append(entry)
        self.reverse_button = self._button(buttons, "↶  逆时针一步", lambda: self._jog(1))
        self.reverse_button.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.forward_button = self._button(buttons, "↷  顺时针一步", lambda: self._jog(-1), primary=True)
        self.forward_button.pack(side="left", fill="x", expand=True)
        target_box = ttk.LabelFrame(parent, text="角度定位 · 输入目标后执行", padding=(8, 6))
        target_box.pack(fill="x", pady=(0, 8))
        row = self._field(target_box, "目标累计角度", self.target, "°")
        self.goto_button = self._button(row, "执行定位", self._goto, primary=True)
        self.goto_button.pack(side="right")
        row = ttk.Frame(parent)
        row.pack(fill="x")
        self.zero_button = self._button(row, "就近回零", self._zero)
        self.zero_button.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.origin_button = self._button(row, "保存为开机零点", self._save_startup_origin)
        self.origin_button.pack(side="left", fill="x", expand=True)

    def _motor_changed(self, _event=None):
        self.controller.armed = None
        self.controller.backend.selected = self.motor_picker.current()
        self.last_error = None
        self._refresh()

    def _pose_layout(self):
        parent = self.pose_tab
        self.pose_actions = []
        self.pose_feedback = tk.StringVar(value="尚未标定机械中心")
        top = ttk.Frame(parent)
        top.pack(fill="x", pady=(0, 6))
        ttk.Label(top, text="同时控制 ID 1 + ID 2", foreground=BLUE).pack(side="left")
        self.enable_all_button = self._button(top, "使能两台电机", lambda: self._perform(self.controller.backend.enable_all, "双轴使能已提交"), managed=False)
        self.enable_all_button.pack(side="right")
        self.pose_tabs = ttk.Notebook(parent, style="Flat.TNotebook")
        self._navigation(parent, self.pose_tabs, ("目标定位", "往复扫描", "标定与范围"))
        self.pose_tabs.pack(fill="both", expand=True)
        self.pose_target_tab = ttk.Frame(self.pose_tabs, padding=8)
        self.pose_scan_tab = ttk.Frame(self.pose_tabs, padding=8)
        self.pose_setup_tab = ttk.Frame(self.pose_tabs, padding=8)
        for page, label in ((self.pose_target_tab, "目标定位"), (self.pose_scan_tab, "往复扫描"), (self.pose_setup_tab, "标定与范围")):
            self.pose_tabs.add(page, text=label)
        parent = self.pose_target_tab
        ttk.Label(parent, text="改下方目标值 → 点击执行。首次使用先到“标定与范围”。", style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        self._field(parent, "目标扇扫 α", self.alpha, "° · 共同命令角")
        self._field(parent, "目标俯仰 β", self.beta, "° · 差动命令角")
        self._field(parent, "定位转速", self.pose_rpm, "RPM")
        ttk.Label(parent, textvariable=self.pose_preview, foreground=BLUE).pack(anchor="w", pady=(0, 6))
        row = ttk.Frame(parent)
        row.pack(fill="x")
        self.pose_move_button = self._button(row, "执行双电机定位", lambda: self._perform(lambda: self.pose.move(self.alpha.get(), self.beta.get(), self.pose_rpm.get()), "双电机定位已提交"), managed=False, primary=True)
        self.pose_move_button.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.pose_zero_button = self._button(row, "双轴回到中心", lambda: self._perform(lambda: self.pose.move(0, 0, self.pose_rpm.get()), "双轴回到中心"), managed=False)
        self.pose_zero_button.pack(side="left", fill="x", expand=True)
        self.pose_actions.extend((self.pose_move_button, self.pose_zero_button))
        ttk.Label(parent, text="纯扇扫：只改 α，β 设 0；纯俯仰：α 设 0，只改 β。", style="Muted.TLabel").pack(anchor="w", pady=(8, 0))
        parent = self.pose_scan_tab
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(0, 6))
        ttk.Label(row, text="扫描方向", width=12).pack(side="left")
        ttk.Combobox(row, textvariable=self.sweep_axis, values=("扇扫 α", "俯仰 β"), state="readonly", width=16).pack(side="left")
        self._field(parent, "往复半幅 ±", self.amplitude, "° · 两端为 −幅度 / +幅度")
        row = self._field(parent, "", self.fixed, "° · 扫描时保持此值")
        row.winfo_children()[0].configure(textvariable=self.fixed_label)
        row = self._field(parent, "往复次数", self.cycles, "次")
        ttk.Label(row, text="转速 RPM").pack(side="left", padx=(10, 5))
        entry = ttk.Entry(row, textvariable=self.sweep_rpm, width=5, style="Input.TEntry")
        entry.pack(side="left")
        self.motion_inputs.append(entry)
        ttk.Label(parent, textvariable=self.sweep_preview, foreground=BLUE, wraplength=490).pack(anchor="w", pady=(0, 6))
        self.pose_scan_button = self._button(parent, "开始双电机往复扫描", self._start_sweep, managed=False, primary=True)
        self.pose_scan_button.pack(fill="x")
        self.pose_actions.append(self.pose_scan_button)
        parent = self.pose_setup_tab
        ttk.Label(parent, text="先用单电机控制调到机械中心，再记录当前位置和方向。", style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        row = ttk.Frame(parent)
        row.pack(fill="x")
        for label, var in (("左轴正向", self.left_sign), ("右轴正向", self.right_sign)):
            ttk.Label(row, text=label).pack(side="left")
            ttk.Combobox(row, textvariable=var, values=("+1", "-1"), width=3, state="readonly").pack(side="left", padx=5)
        self.calibrate_button = self._button(row, "标定机械中心", self._calibrate_pose, managed=False)
        self.calibrate_button.pack(side="right")
        self.pose_actions.append(self.calibrate_button)
        box = ttk.LabelFrame(parent, text="实验软件限位（可选，按测量结果填写）", padding=8)
        box.pack(fill="x", pady=(8, 4))
        ttk.Checkbutton(box, text="启用下方软件限位", variable=self.use_limits).pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=4)
        for label, variable in (("扇扫 α ±", self.alpha_limit), ("俯仰 β ±", self.beta_limit)):
            ttk.Label(row, text=label).pack(side="left", padx=(0, 5))
            ttk.Entry(row, textvariable=variable, width=6, style="Input.TEntry").pack(side="left")
            ttk.Label(row, text="°").pack(side="left", padx=(2, 8))
        self.apply_limits_button = self._button(box, "应用实验范围设置", self._apply_limits, managed=False)
        self.apply_limits_button.pack(fill="x")
        ttk.Label(parent, textvariable=self.limit_status, style="Muted.TLabel", wraplength=490).pack(anchor="w")
        ttk.Label(self.pose_tab, textvariable=self.pose_feedback, style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

    def _parameter_hints(self, *_args):
        try:
            a = number(self.alpha.get(), "α", -360000, 360000)
            b = number(self.beta.get(), "β", -360000, 360000)
            self.pose_preview.set(f"中心相对电机命令：左轴 {a+b:+g}°，右轴 {a-b:+g}°（标定方向前）")
        except ValueError:
            self.pose_preview.set("输入有效的扇扫 α 和俯仰 β 后显示电机命令")
        name = "俯仰 β" if self.sweep_axis.get() == "扇扫 α" else "扇扫 α"
        self.fixed_label.set("固定" + name)
        self.sweep_preview.set(f"扫描：−{self.amplitude.get()}° → +{self.amplitude.get()}° → −{self.amplitude.get()}°，共 {self.cycles.get()} 次；完成后扫描轴归中。")

    def _start_sweep(self):
        if self._perform(lambda: self.pose.sweep("alpha" if self.sweep_axis.get() == "扇扫 α" else "beta", self.amplitude.get(), self.fixed.get(), self.sweep_rpm.get(), self.cycles.get()), "双电机往复扫描已启动"):
            self._scanning = True
            self._scan_total = len(self.pose.sequence)+1
            self._refresh()

    def _apply_limits(self):
        def apply():
            self.pose.configure_limits(self.alpha_limit.get(), self.beta_limit.get()) if self.use_limits.get() else self.pose.configure_limits()
            limits = self.pose.limits
            self.limit_status.set(f"已应用软件限位：α ±{limits[0]:g}°，β ±{limits[1]:g}°（实验设置）" if limits else "未启用软件限位；机械范围待实验测量")
        self._perform(apply, "实验范围设置已应用")

    def _workspace_changed(self, _event=None):
        dual = self.workspaces.index(self.workspaces.select()) == 1
        # Workspace changes cancel queued segments and disarm single-axis starts.
        self.pose.sequence = []
        self._scanning = False
        self.controller.stop()
        if dual:
            self.single_feedback.pack_forget()
            self.dual_feedback.pack(fill="both", expand=True)
        else:
            self.dual_feedback.pack_forget()
            self.single_feedback.pack(fill="both", expand=True)
        self._mode_changed()
        self._refresh()

    def _calibrate_pose(self):
        self._perform(lambda: self.pose.calibrate(int(self.left_sign.get()), int(self.right_sign.get())), "已记录机械中心和方向；α/β为电机命令角")

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
        self.logs.see("end-2c")
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
        self._scanning = False
        if self.controller.snapshot().connected:
            self._perform(self.controller.disconnect, "已断开" if isinstance(self.controller.backend, DualDemoBackend) else "正在断开")
        else:
            def connect():
                if self.connection_mode.get() == "ST-Link 连接":
                    probe, elf = self.port.get(), self.elf_path.get()
                    backend = DualSerialBackend(probe, pulses_per_turn=self.pulses.get(),
                        serial_factory=lambda *_a, **_k: BridgeTransport(probe, elf, progress=lambda message: setattr(backend, "activity", message)))
                elif self.connection_mode.get() == "串口连接":
                    backend = DualSerialBackend(self.port.get(), self.baudrate.get(), self.pulses.get())
                else:
                    backend = DualDemoBackend()
                backend.selected = self.motor_picker.current()
                self.controller.select_backend(backend)
                previous_limits = self.pose.limits
                self.pose = PoseControl(backend)
                self.pose.limits = previous_limits
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
        self._perform(lambda: self.controller.return_zero(self.position_rpm.get()), "所选电机就近回零已提交")

    def _save_startup_origin(self) -> None:
        self.pose.center = None
        self._saving_startup_origin = True
        if not self._perform(self.controller.save_startup_origin, "正在保存开机零点"):
            self._saving_startup_origin = False

    def _origin(self) -> None:
        self.pose.center = None
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
        self._scanning = False
        self.pose.sequence = []
        self._perform(self.controller.stop, "两台电机停止请求已提交")

    def _mode_changed(self, _event=None) -> None:
        if self.pose.sequence:
            self.pose.stop()
        mode = "continuous" if self.workspaces.index(self.workspaces.select()) == 0 and self.tabs.index(self.tabs.select()) == 1 else "position"
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
        states = [axis.snapshot() for axis in self.controller.backend.axes]
        busy = bool(self.pose.sequence) or any(s.motion != "idle" for s in states)
        dual = self.workspaces.index(self.workspaces.select()) == 1
        self._activity(states, busy, dual)
        for notebook, buttons, prominent in self.navigation:
            selected = notebook.index(notebook.select())
            for index, button in enumerate(buttons):
                active = index == selected
                style = ("NavSelectedLarge.TButton" if active else "NavLarge.TButton") if prominent else ("NavSelected.TButton" if active else "Nav.TButton")
                button.configure(style=style, state="disabled" if busy and prominent else "normal")
        for entry in self.motion_inputs:
            entry.configure(state="disabled" if busy else "normal")
        for axis_state, (position, status, target) in zip(states, self.motor_cards):
            position.set(f"{axis_state.position_deg:+.3f}°" if axis_state.connected else "—")
            status.set(("已使能" if axis_state.enabled else "未使能") if axis_state.connected else "离线")
            target.set(f"目标 {axis_state.target_deg:+.3f}°" if axis_state.connected and axis_state.target_deg is not None else "目标 —")
        self.status_title.set("双电机反馈 · ID 1 + ID 2" if dual else f"单电机反馈 · ID {self.controller.backend.selected+1}")
        self.motor_picker.configure(state="disabled" if busy else "readonly")
        self.apply_limits_button.configure(state="disabled" if busy else "normal")
        motor_lines = []
        for i, axis_state in enumerate(states):
            title = f"电机 {i+1} · {'左轴' if i == 0 else '右轴'}"
            if not axis_state.connected:
                motor_lines.append(title + "：离线")
                continue
            target = f"{axis_state.target_deg:+.3f}°" if axis_state.target_deg is not None else "—"
            motion = {"idle": "待机", "positioning": "定位中", "pending": "处理中", "continuous": "旋转中", "stopping": "停止中"}.get(axis_state.motion, "处理中")
            motor_lines.append(f"{title}\n当前位置：{axis_state.position_deg:+.3f}°\n目标角度：{target}\n状态：{motion} · {'使能' if axis_state.enabled else '失能'}")
        self.dual_status.set("\n\n".join(motor_lines) + "\n\n" + self.limit_status.get())
        self.dual_info.set("  |  ".join(f"ID {i+1}: {s.position_deg:+.3f}° · {'使能' if s.enabled else '失能'}" if s.connected else f"ID {i+1}: 离线" for i,s in enumerate(states)))
        coords = self.pose.coordinates() if all(s.connected for s in states) else None
        self.pose_feedback.set(f"反馈命令坐标 α={coords[0]:+.3f}°  β={coords[1]:+.3f}°" if coords else "离线：无实时姿态反馈" if not all(s.connected for s in states) else "尚未标定机械中心")
        self.enable_all_button.configure(state="normal" if all(s.connected for s in states) and not busy else "disabled")
        dual_ready = all(s.connected and s.enabled and not s.error for s in states) and not busy
        for button in self.pose_actions:
            button.configure(state="normal" if dual_ready else "disabled")
        ready = state.connected and state.enabled and state.motion == "idle" and not busy
        for button in self.actions:
            button.configure(state="normal" if ready else "disabled")
        self.origin_button.configure(state="normal" if state.connected and state.motion == "idle" and not busy else "disabled")
        self.enable_button.configure(state="normal" if state.connected and state.motion == "idle" and not busy else "disabled", text="失能所选电机" if state.enabled else "使能所选电机")
        busy_connection = state.motion in ("connecting", "closing")
        self.connect_button.configure(text="断开" if state.connected else "连接", state="disabled" if busy_connection else "normal")
        if state.connected:
            self.settings_row.pack_forget()
        elif not self.settings_row.winfo_manager():
            self.settings_row.pack(fill="x", pady=(6, 0))
        locked = state.connected or busy_connection
        self.mode_picker.configure(state="disabled" if locked else "readonly")
        serial_locked = locked or self.connection_mode.get() == "模拟连接"
        for entry in self.serial_entries:
            entry.configure(state="disabled" if serial_locked else "normal")
        self.scan_button.configure(state="disabled" if serial_locked or self.probe_scanning else "normal")
        self.elf_button.configure(state="disabled" if locked else "normal")
        self.connection.set("正在连接" if state.motion == "connecting" else "正在断开" if state.motion == "closing" else "已连接" if state.connected else "未连接")
        phase = round(state.position_deg % 360.0, 3) % 360.0
        self.angle.set(f"{phase:.3f}°" if state.connected else "—")
        self.angle_caption.set(f"单圈角度 · 累计 {state.position_deg:+.3f}°" if state.connected else "单圈角度 · 累计角度 —")
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
        try:
            self.pose.update()
        except (ValueError, RuntimeError) as exc:
            self.notice.set(str(exc))
            self.notice_label.configure(foreground=RED)
            self._log(f"扫描已停止 · {exc}")
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
        all_idle = all(axis.snapshot().motion == "idle" for axis in self.controller.backend.axes) and not self.pose.sequence
        if self._scanning and all_idle and state.connected and not state.error:
            self._scanning = False
            self.notice.set("往复扫描已完成")
            self._log("往复扫描已完成")
        elif self.last_motion in ("positioning", "continuous", "confirming", "stopping") and motion == "idle" and all_idle and state.connected and not state.error:
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
