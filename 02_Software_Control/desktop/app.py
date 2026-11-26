"""Run with python -m desktop.app. Uses only the Python standard library."""
from __future__ import annotations

import math
import time
import tkinter as tk
from tkinter import messagebox, ttk

if __package__:
    from .controller import Controller, firmware_default_rpm
else:
    from controller import Controller, firmware_default_rpm


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
        self.title("Motor Desk · 电机控制台（演示版）")
        self.geometry("1180x950")
        self.minsize(1060, 950)
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
        self.notice = tk.StringVar(value="先连接演示设备，再使能电机。所有操作均为模拟。")
        self.connection = tk.StringVar(value="未连接")
        self.angle = tk.StringVar(value="—")
        self.info: dict[str, tk.StringVar] = {}
        self.actions: list[ttk.Button] = []
        self._styles()
        self._layout()
        for var in (self.continuous_rpm, self.duration, self.direction):
            var.trace_add("write", self._invalidate_lock)
        self.bind("<Escape>", lambda _event: self._stop())
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._log(f"界面就绪 · 固件默认转速 {self.default_rpm} RPM · 未连接真实电机")
        self._refresh()
        self.tick_id = self.after(50, self._tick)

    def _styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        self.option_add("*Font", ("Microsoft YaHei UI", 10))
        style.configure("TFrame", background=WHITE)
        style.configure("TLabel", background=WHITE, foreground=INK, font=("Microsoft YaHei UI", 10))
        style.configure("Muted.TLabel", foreground=MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("Section.TLabel", font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(12, 9),
                        background="#EAF0FC", foreground=INK, borderwidth=0)
        style.map("TButton", background=[("active", "#DCE7FF"), ("disabled", "#F1F3F7")],
                  foreground=[("disabled", "#9AA7B9")])
        style.configure("Primary.TButton", background=BLUE, foreground=WHITE)
        style.map("Primary.TButton", background=[("disabled", "#D5DFF4"), ("active", "#174AC7")],
                  foreground=[("disabled", "#8799BB")])
        style.configure("Stop.TButton", background=RED, foreground=WHITE, font=("Microsoft YaHei UI", 12, "bold"))
        style.map("Stop.TButton", background=[("active", "#AD2531")])
        style.configure("TEntry", padding=7, fieldbackground="#F8FAFD", bordercolor=LINE)
        style.configure("TNotebook", background=WHITE, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(20, 11), font=("Microsoft YaHei UI", 11))
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
        header = tk.Frame(self, bg="#142747", padx=26, pady=12)
        header.pack(fill="x")
        tk.Label(header, text="MOTOR DESK", bg="#142747", fg="#A9C2FF",
                 font=("Segoe UI", 10, "bold")).pack(anchor="w")
        line = tk.Frame(header, bg="#142747")
        line.pack(fill="x")
        tk.Label(line, text="电机控制台", bg="#142747", fg=WHITE,
                 font=("Microsoft YaHei UI", 23, "bold")).pack(side="left")
        ttk.Button(line, text="■  停止 / Esc", style="Stop.TButton", command=self._stop).pack(side="right")
        tk.Label(header, text="角度定位  /  连续旋转     ·     F407 控制项目", bg="#142747", fg="#B7C5DC",
                 font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(4, 0))

        demo = tk.Frame(self, bg="#FFF1D7", padx=25, pady=8)
        demo.pack(fill="x")
        tk.Label(demo, text="演示版 · 不发送硬件指令", bg="#FFF1D7", fg="#8C5D14",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        tk.Label(demo, text="角度与运行状态为模拟数据；真实串口待接入", bg="#FFF1D7", fg="#8C5D14",
                 font=("Microsoft YaHei UI", 9)).pack(side="right")

        content = tk.Frame(self, bg=BG, padx=22, pady=16)
        content.pack(fill="both", expand=True)
        connection = ttk.Frame(content, padding=(16, 10))
        connection.pack(fill="x", pady=(0, 14))
        self._label(connection, "设备连接", "Section.TLabel").pack(side="left", padx=(0, 18))
        self._label(connection, "演示设备  /  本地模拟").pack(side="left")
        self._label(connection, " · ", "Muted.TLabel").pack(side="left")
        ttk.Label(connection, textvariable=self.connection, foreground=BLUE).pack(side="left")
        self.connect_button = self._button(connection, "连接演示设备", self._connect, primary=True, managed=False)
        self.connect_button.pack(side="right")
        self.enable_button = self._button(connection, "使能电机", self._enable, managed=False)
        self.enable_button.pack(side="right", padx=10)

        footer = tk.Frame(content, bg=BG)
        footer.pack(side="bottom", fill="x")
        body = tk.Frame(content, bg=BG)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3, minsize=590)
        body.columnconfigure(1, weight=2, minsize=400)
        body.rowconfigure(0, weight=1)
        left = ttk.Frame(body, padding=18)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        self._label(left, "运动控制", "Section.TLabel").pack(anchor="w", pady=(0, 12))
        self.tabs = ttk.Notebook(left)
        self.tabs.pack(fill="both", expand=True)
        self.position_tab = ttk.Frame(self.tabs, padding=(12, 10))
        self.continuous_tab = ttk.Frame(self.tabs, padding=(12, 10))
        self.tabs.add(self.position_tab, text="01  角度定位")
        self.tabs.add(self.continuous_tab, text="02  连续旋转")
        self._position_layout()
        self._continuous_layout()
        self.tabs.bind("<<NotebookTabChanged>>", self._mode_changed)

        right = ttk.Frame(body, padding=18)
        right.grid(row=0, column=1, sticky="nsew")
        self._label(right, "电机信息", "Section.TLabel").pack(anchor="w")
        self.dial = tk.Canvas(right, height=80, bg=WHITE, highlightthickness=0)
        self.dial.pack(fill="x", pady=(6, 0))
        ttk.Label(right, textvariable=self.angle, font=("Segoe UI", 24, "bold"), foreground=BLUE).pack()
        self._label(right, "当前角度 · 累计角度（模拟）", "Muted.TLabel").pack(pady=(0, 9))
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
        notice.pack(fill="x", pady=(12, 8))
        self.notice_label = ttk.Label(notice, textvariable=self.notice, foreground=BLUE, wraplength=1040)
        self.notice_label.pack(anchor="w")
        log_header = tk.Frame(footer, bg=BG)
        log_header.pack(fill="x", pady=(0, 5))
        tk.Label(log_header, text="操作日志", bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(side="left")
        tk.Label(log_header, text="软件停止通过通信实现，实机不等同于硬件急停。", bg=BG, fg=MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="right")
        log_frame = ttk.Frame(footer)
        log_frame.pack(fill="x")
        self.logs = tk.Text(log_frame, height=3, wrap="word", bg="#EAF0F8", fg=MUTED, relief="flat",
                            padx=10, pady=7, font=("Microsoft YaHei UI", 9), state="disabled")
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
        self._label(parent, "每次点击执行一次，运行中不接受重复运动。", "Muted.TLabel").pack(anchor="w", pady=(0, 10))
        self._field(parent, "单步角度", self.step, "°")
        buttons = ttk.Frame(parent)
        buttons.pack(fill="x", pady=(0, 10))
        self.reverse_button = self._button(buttons, "↶  反转一步", lambda: self._jog(-1))
        self.reverse_button.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.forward_button = self._button(buttons, "↷  正转一步", lambda: self._jog(1), primary=True)
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
        self.origin_button = self._button(row, "设当前位置为零点", self._origin)
        self.origin_button.pack(side="left", fill="x", expand=True)
        self._label(parent, "复位返回当前坐标零点；不执行机械寻零。", "Muted.TLabel").pack(anchor="w", pady=(8, 0))

    def _continuous_layout(self) -> None:
        parent = self.continuous_tab
        self._label(parent, "先设置参数，再解锁；每次启动后自动重新上锁。", "Muted.TLabel").pack(anchor="w", pady=(0, 12))
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(0, 10))
        self._label(row, "旋转方向", width=12).pack(side="left")
        self.direction_radios = []
        for text, value in (("正转 +", 1), ("反转 −", -1)):
            radio = ttk.Radiobutton(row, text=text, variable=self.direction, value=value)
            radio.pack(side="left", padx=(0, 20))
            self.direction_radios.append(radio)
        row = self._field(parent, "连续转速", self.continuous_rpm, "RPM")
        self._button(row, f"默认 {self.default_rpm} RPM", lambda: self._default(self.continuous_rpm), managed=False).pack(side="right")
        self._field(parent, "最长运行时间", self.duration, "秒 · 0.1 ～ 60")
        ttk.Separator(parent).pack(fill="x", pady=(3, 10))
        self.lock_check = ttk.Checkbutton(parent, text="我已确认方向与参数，解锁本次启动", variable=self.unlock, command=self._arm)
        self.lock_check.pack(anchor="w", pady=(0, 8))
        self.lock_text = tk.StringVar(value="保护锁已锁定")
        ttk.Label(parent, textvariable=self.lock_text, foreground="#9C671C").pack(anchor="w", pady=(0, 12))
        self.run_button = self._button(parent, "启动连续旋转", self._rotate, primary=True)
        self.run_button.pack(fill="x")

    def _log(self, text: str) -> None:
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

    def _connect(self) -> None:
        if self.controller.snapshot().connected:
            self._perform(self.controller.disconnect, "演示设备已断开，模拟运动已停止")
        else:
            self._perform(self.controller.connect, "演示设备已连接 · 请使能电机后操作")

    def _enable(self) -> None:
        enabled = not self.controller.snapshot().enabled
        self._perform(lambda: self.controller.enable(enabled), "模拟电机已使能" if enabled else "模拟电机已失能，运动已停止")

    def _jog(self, direction: int) -> None:
        self._perform(lambda: self.controller.jog(direction, self.step.get(), self.position_rpm.get()),
                      f"模拟{'正' if direction == 1 else '反'}转一步 · {self.step.get()}° · {self.position_rpm.get()} RPM")

    def _goto(self) -> None:
        self._perform(lambda: self.controller.move_to(self.target.get(), self.position_rpm.get()),
                      f"模拟定位至 {self.target.get()}° · {self.position_rpm.get()} RPM")

    def _zero(self) -> None:
        self._perform(lambda: self.controller.return_zero(self.position_rpm.get()), "模拟复位 · 返回当前坐标 0°")

    def _origin(self) -> None:
        if messagebox.askyesno("设置坐标零点", "将当前停止位置设为 0°？\n\n这会更改坐标参考，不会转动电机，也不执行机械寻零。", parent=self):
            self._perform(self.controller.set_origin, "当前位置已设为模拟坐标零点")

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
                      "本次连续旋转已解锁 · 点击启动执行")

    def _rotate(self) -> None:
        self._perform(lambda: self.controller.rotate(self.direction.get(), self.continuous_rpm.get(), self.duration.get()),
                      f"模拟连续旋转已启动 · {self.continuous_rpm.get()} RPM · 最长 {self.duration.get()} 秒")

    def _stop(self) -> None:
        self._perform(self.controller.stop, "停止 · 模拟运动已停止，连续旋转保护锁已锁定")

    def _mode_changed(self, _event=None) -> None:
        mode = "position" if self.tabs.index(self.tabs.select()) == 0 else "continuous"
        if mode != self.controller.mode:
            self._perform(lambda: self.controller.change_mode(mode),
                          f"已切换到{'角度定位' if mode == 'position' else '连续旋转'}模式 · 运动停止，保护锁已锁定")

    def _draw_dial(self, angle: float | None) -> None:
        canvas = self.dial
        canvas.delete("all")
        cx, cy, radius = max(canvas.winfo_width(), 300) / 2, 40, 30
        canvas.create_oval(cx-radius, cy-radius, cx+radius, cy+radius, outline=LINE, width=10)
        for i in range(12):
            radians = i * math.pi / 6 - math.pi / 2
            canvas.create_line(cx+20*math.cos(radians), cy+20*math.sin(radians),
                               cx+26*math.cos(radians), cy+26*math.sin(radians), fill="#A6B6CE", width=2)
        radians = math.radians((angle or 0) % 360) - math.pi / 2
        canvas.create_line(cx, cy, cx+19*math.cos(radians), cy+19*math.sin(radians),
                           fill=BLUE if angle is not None else "#B6C3D6", width=4, capstyle="round")
        canvas.create_oval(cx-5, cy-5, cx+5, cy+5, fill=BLUE if angle is not None else "#B6C3D6", outline="")

    def _refresh(self) -> None:
        state = self.controller.snapshot()
        ready = state.connected and state.enabled and state.motion == "idle"
        for button in self.actions:
            button.configure(state="normal" if ready else "disabled")
        self.origin_button.configure(state="normal" if state.connected and state.motion == "idle" else "disabled")
        self.enable_button.configure(state="normal" if state.connected else "disabled", text="失能电机" if state.enabled else "使能电机")
        self.connect_button.configure(text="断开演示设备" if state.connected else "连接演示设备")
        self.connection.set("已连接 · 模拟" if state.connected else "未连接")
        self.angle.set(f"{state.position_deg:+.3f}°" if state.connected else "—")
        motion_text = {"idle": "待机", "positioning": "角度定位中", "continuous": "连续旋转中"}[state.motion]
        self.info["motion"].set(motion_text if state.connected else "离线")
        self.info["enabled"].set(("已使能" if state.enabled else "未使能") if state.connected else "—")
        self.info["target"].set(f"{state.target_deg:+.3f}°" if state.target_deg is not None else "—")
        self.info["speed"].set(f"{state.speed_rpm:g} RPM（模拟）" if state.connected else "—")
        self.info["remaining"].set(f"{state.remaining_s:.1f} s" if state.remaining_s is not None else "—")
        self.info["voltage"].set("— / —（无实测数据）")
        self.info["origin"].set("零点：" + (state.origin if state.connected else "未连接"))
        armed = self.controller.armed is not None
        self.unlock.set(armed)
        self.lock_text.set("本次启动已解锁" if armed else "保护锁已锁定")
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
        if self.last_motion != "idle" and motion == "idle":
            message = "模拟定位已到达目标" if self.last_motion == "positioning" else "达到最长运行时间，模拟旋转已停止"
            self.notice.set(message)
            self.notice_label.configure(foreground=BLUE)
            self._log(message)
        self.last_motion = motion

    def _tick(self) -> None:
        self._advance()
        self._refresh()
        self.tick_id = self.after(50, self._tick)

    def _close(self) -> None:
        self.after_cancel(self.tick_id)
        self.controller.disconnect()
        self.destroy()


def main() -> None:
    # Avoid blurry text on Windows; use Tk's normal scaling for the chosen DPI.
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    MotorApp().mainloop()


if __name__ == "__main__":
    main()
