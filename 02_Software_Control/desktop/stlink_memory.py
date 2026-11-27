"""Persistent ST-Link SWD memory access, with no halt/reset/flash operations.

Uses pyOCD's ST-Link command layer directly, never a target Session (whose
initialisation can halt a core). Keep all USB access on the bus worker thread.
"""
class StLinkMemory:
    def __init__(self, serial, *, link=None):
        self.link = link
        self.memory = None
        try:
            if self.link is None:
                from pyocd.probe.stlink.usb import STLinkUSBInterface
                from pyocd.probe.stlink.stlink import STLink
                devices = STLinkUSBInterface.get_all_connected_devices()
                device = next((d for d in devices if d.serial_number.upper() == serial.upper()), None)
                if device is None:
                    raise RuntimeError("未找到所选 ST-Link；请关闭其他调试/电机控制会话后重试")
                self.link = STLink(device)
            from pyocd.probe.stlink_probe import STLinkMemoryInterface
            self.link.open()
            self.link.enter_debug(self.link.Protocol.SWD)
            self.link.set_swd_frequency(1000000)
            self.link.open_ap(0)
            self.memory = STLinkMemoryInterface(self.link, 0)
        except Exception as exc:
            try:
                self.close()
            except Exception:
                pass
            raise RuntimeError(f"ST-Link 持续连接失败：{exc}") from None

    def read(self, address, size):
        if size <= 0:
            raise ValueError("读取长度无效")
        try:
            return bytes(self.memory.read_memory_block8(address, size))
        except Exception as exc:
            raise RuntimeError(f"ST-Link 读取失败：{exc}") from None

    def write(self, address, data):
        if not data or not 0x20000000 <= address < address+len(data) <= 0x20020000:
            raise ValueError("实时控制只允许写入 F407 SRAM")
        try:
            self.memory.write_memory_block8(address, data)
        except Exception as exc:
            raise RuntimeError(f"ST-Link 写入失败：{exc}") from None

    def close(self):
        link, self.link = self.link, None
        if link is not None:
            link.close()
