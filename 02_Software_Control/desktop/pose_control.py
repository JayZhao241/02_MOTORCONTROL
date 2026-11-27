"""Common/differential motor command coordinates; not probe output angles."""
from .controller import number, Controller


class PoseControl:
    def __init__(self, backend):
        self.backend = backend
        self.center = None
        self.signs = (1, 1)
        self.sequence = []
        self.limits = None

    def ready(self):
        states = [a.snapshot() for a in self.backend.axes]
        if not all(s.connected and s.enabled and s.motion == "idle" and not s.error for s in states):
            raise ValueError("两台电机须连接、使能并停稳")
        return states

    def calibrate(self, left_sign, right_sign):
        states = self.ready()
        if left_sign not in (-1, 1) or right_sign not in (-1, 1):
            raise ValueError("方向须为 +1 或 -1")
        self.center = tuple(s.position_deg for s in states)
        self.signs = (left_sign, right_sign)
        self.sequence = []

    def coordinates(self):
        if self.center is None:
            return None
        q = [(a.snapshot().position_deg-c)*s for a,c,s in zip(self.backend.axes,self.center,self.signs)]
        return ((q[0]+q[1])/2, (q[0]-q[1])/2)

    def configure_limits(self, alpha=None, beta=None):
        if self.sequence or any(a.snapshot().motion != "idle" for a in self.backend.axes):
            raise ValueError("请先停止运动再修改实验范围")
        if alpha is None and beta is None:
            self.limits = None
        else:
            limits = (number(alpha, "扇扫软件限位 ±", 0.001, 360000),
                      number(beta, "俯仰软件限位 ±", 0.001, 360000))
            self.limits = limits

    def _coordinates_in_range(self, alpha, beta):
        a = number(alpha, "扇扫命令 α（度）", -360000, 360000)
        b = number(beta, "俯仰命令 β（度）", -360000, 360000)
        if self.limits and (abs(a) > self.limits[0] or abs(b) > self.limits[1]):
            raise ValueError(f"目标超出已应用的软件限位：α ±{self.limits[0]:g}°，β ±{self.limits[1]:g}°")
        return a, b

    def move(self, alpha, beta, rpm):
        self.ready()
        if self.center is None:
            raise ValueError("先将机构置于机械中心并标定中心与方向")
        a, b = self._coordinates_in_range(alpha, beta)
        speed = Controller._rpm(rpm)
        current_a, current_b = self.coordinates()
        if self.limits and (abs(current_a) > self.limits[0]+0.2 or abs(current_b) > self.limits[1]+0.2):
            raise ValueError("当前命令坐标超出软件限位，请调整实验范围或用单轴控制回到范围内")
        # Move one command coordinate at a time: equal motor distances keep
        # the other coordinate fixed throughout each synchronized segment.
        following = None
        if abs(a-current_a) > 0.12 and abs(b-current_b) > 0.12:
            following = (a,b,speed)
            b = current_b
        targets = tuple(c+s*q for c,s,q in zip(self.center,self.signs,(a+b,a-b)))
        self.backend.move_pair(targets,speed)
        if following:
            self.sequence.insert(0,following)

    def sweep(self, axis, amplitude, fixed, rpm, cycles):
        if axis not in ("alpha", "beta"):
            raise ValueError("请选择扇扫或俯仰")
        amp = number(amplitude,"往复半幅",0.001,360000)
        count = number(cycles,"往复次数",1,100)
        if not count.is_integer():
            raise ValueError("往复次数请输入整数")
        hold = number(fixed,"固定命令角",-360000,360000)
        self._coordinates_in_range(amp, hold) if axis == "alpha" else self._coordinates_in_range(hold, amp)
        points = [(v,hold,rpm) if axis == "alpha" else (hold,v,rpm) for v in (-amp,amp,-amp)]
        self.sequence = []
        self.move(*points[0])
        self.sequence += (points[1:]*int(count))+[(0,hold,rpm) if axis == "alpha" else (hold,0,rpm)]

    def update(self):
        if not self.sequence:
            return
        states = [a.snapshot() for a in self.backend.axes]
        if any(s.error or not s.connected or not s.enabled for s in states):
            self.stop()
        elif all(s.motion == "idle" for s in states):
            try:
                self.move(*self.sequence.pop(0))
            except Exception:
                self.stop()
                raise

    def stop(self):
        self.sequence = []
        self.backend.stop()
