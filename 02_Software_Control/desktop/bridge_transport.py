"""Serial-compatible exclusive SWD mailbox transport for the dual-axis worker."""
import time
from .stlink_backend import WatchLink, find_tool, run_tool


class BridgeLink(WatchLink):
    def _resolve_layout(self):
        symbol = "s_bridge"
        names = ("request", "arm", "tx_len", "rx_len", "result", "active", "tx", "rx",
                 "driver_state", "hal_error", "hal_status", "driver_error", "tx_result",
                 "stop_requested_mask", "stop_failed_mask")
        args = [find_tool("arm-none-eabi-gdb"), "-q", "-nx", "-batch", str(self.elf)]
        for expr in [f"(unsigned long)&{symbol}", f"sizeof({symbol})"] + [v for n in names for v in (f"(unsigned long)&{symbol}.{n}",f"sizeof({symbol}.{n})")]:
            args += ["-ex", "p/x "+expr]
        import re
        values = [int(v,16) for v in re.findall(r"\$\d+ = (0x[0-9a-fA-F]+)",run_tool(args))]
        if len(values) != 2+2*len(names):
            raise RuntimeError("固件缺少双轴总线接口，请构建并烧录新版 Debug 固件")
        self.base,self.size = values[:2]
        if not 0x20000000 <= self.base < self.base+self.size <= 0x20020000:
            raise RuntimeError("双轴邮箱地址超出 SRAM")
        self.layout = {name:tuple(values[2+i*2:4+i*2]) for i,name in enumerate(names)}
        for name,(address,size) in self.layout.items():
            expected = 20 if name == "tx" else 128 if name == "rx" else 4
            if size != expected or not self.base <= address <= self.base+self.size-size:
                raise RuntimeError("双轴邮箱字段布局无效")

    def read(self):
        # SWD block reads are not atomic with respect to the running MCU.
        # The MCU may complete between reading rx_len and result. Only accept
        # a completed payload if its header stays unchanged across the read.
        for _ in range(8):
            data = self.read_memory(self.base,self.size)
            pending = int.from_bytes(data[0:4],"little") != 0 or int.from_bytes(data[16:20],"little") == 1
            if pending or self.read_memory(self.base,24) == data[:24]:
                state = {name:(data[address-self.base:address-self.base+size] if name in ("tx","rx") else
                              int.from_bytes(data[address-self.base:address-self.base+size],"little"))
                         for name,(address,size) in self.layout.items()}
                if not 0 <= state["rx_len"] <= 128:
                    raise RuntimeError("总线回复长度无效")
                return state
            self.snapshot_retries = getattr(self,"snapshot_retries",0)+1
        raise RuntimeError("总线反馈持续变化，未取得一致快照")

    def publish(self, request, packet=None):
        args = []
        if packet is not None:
            address,_ = self.layout["tx"]
            args += ["-w8",hex(address),*[hex(b) for b in packet]]
            address,_ = self.layout["tx_len"]
            args += ["-w32",hex(address),hex(len(packet))]
        for name,value in (("result",1),("arm",0x4455414C),("request",request)):
            args += ["-w32",hex(self.layout[name][0]),hex(value)]
        self._call(*args,"-nv")

    @staticmethod
    def failure_message(state):
        result = state["result"]
        label = {1: "总线忙", 2: "接口未就绪", 3: "参数无效", 4: "通信超时",
                 5: "接收缓冲溢出", 6: "STM32 串口硬件错误"}.get(result, "未知错误")
        flags = state.get("hal_error", 0)
        names = [name for bit,name in ((1,"PE 校验"),(2,"NE 噪声"),(4,"FE 帧错误"),(8,"ORE 溢出"),(16,"DMA")) if flags & bit]
        details = f"；HAL=0x{flags:02X}" + ("（"+" / ".join(names)+"）" if names else "")
        action = "；确认机构停稳，检查两台电机 Response 的 Reached 回报与接线，再复位控制板后重连" if result == 6 else ""
        if state.get("stop_failed_mask"):
            action += "；停止请求未全部送达，不能确认两轴已停止"
        return f"双轴总线请求失败：{result}（{label}）{details}{action}"

    def wait(self):
        deadline = time.monotonic()+5
        while True:
            state = self.read()
            if state["request"] == 0 and state["result"] != 1:
                if state["result"] != 0:
                    raise RuntimeError(self.failure_message(state))
                return state
            if time.monotonic() >= deadline:
                raise RuntimeError("双轴总线邮箱超时")
            time.sleep(0.01)


class BridgeTransport:
    def __init__(self, probe, elf, *, link_factory=None, progress=None):
        report = progress or (lambda _message: None)
        report("正在打开 ST-Link 通道")
        self.link = (link_factory or create_bridge_link)(probe,elf)
        self.buffer = bytearray()
        self.closed = False
        self.transport_name = getattr(self.link,"transport_name","ST-Link 兼容模式")
        self.last_exchange_ms = None
        self.last_exchange_state = None
        try:
            report("正在核对板上固件")
            self.link.verify_firmware()
            self.link.check_application()
            report("正在连接电机总线")
            self.link.publish(1)
            self.link.wait()
        except Exception:
            close = getattr(self.link,"close_connection",None)
            if close:
                close()
            raise

    def reset_input_buffer(self):
        self.buffer.clear()

    def write(self, packet):
        started = time.monotonic()
        self.link.publish(2,packet)
        try:
            state = self.link.wait()
        except RuntimeError as exc:
            raise RuntimeError(f"ID {packet[0]} / 命令 0x{packet[1]:02X}：{exc}") from None
        self.last_exchange_state = state
        self.last_exchange_ms = (time.monotonic()-started)*1000
        self.buffer.extend(state["rx"][:state["rx_len"]])
        return len(packet)

    def read(self, count):
        data = bytes(self.buffer[:count])
        del self.buffer[:count]
        return data

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.link.publish(3)
                self.link.wait()
            finally:
                close = getattr(self.link,"close_connection",None)
                if close:
                    close()


class PersistentBridgeLink(BridgeLink):
    """Same firmware mailbox over one USB connection for the whole session."""
    transport_name = "ST-Link 直连"

    def __init__(self, serial, elf, *, memory_factory=None):
        self._memory = None
        super().__init__(serial, elf)
        if memory_factory is None:
            from .stlink_memory import StLinkMemory
            memory_factory = StLinkMemory
        self._memory = memory_factory(serial)

    def read_memory(self, address, size):
        return self._memory.read(address, size)

    def publish(self, request, packet=None):
        if request not in (1, 2, 3):
            raise ValueError("未知总线请求")
        if packet is not None:
            if not 3 <= len(packet) <= 20:
                raise ValueError("总线帧长度无效")
            self._memory.write(self.layout["tx"][0], bytes(packet))
            self._memory.write(self.layout["tx_len"][0], len(packet).to_bytes(4,"little"))
        self._memory.write(self.layout["result"][0], (1).to_bytes(4,"little"))
        self._memory.write(self.layout["arm"][0], (0x4455414C).to_bytes(4,"little"))
        # A single aligned word publishes the request after all payload writes.
        self._memory.write(self.layout["request"][0], request.to_bytes(4,"little"))

    def close_connection(self):
        if self._memory is not None:
            self._memory.close()
            self._memory = None


def create_bridge_link(serial, elf):
    try:
        import pyocd  # noqa: F401; dependency check before touching the probe.
    except ImportError:
        link = BridgeLink(serial,elf)
        link.transport_name = "ST-Link 兼容模式（安装依赖可提速）"
        return link
    return PersistentBridgeLink(serial,elf)
