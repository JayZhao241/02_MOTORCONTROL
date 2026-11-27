from types import SimpleNamespace
import unittest
from unittest.mock import patch

from desktop.serial_backend import serial_port_details


class PortDiscoveryTests(unittest.TestCase):
    def test_usb_devices_are_named_and_sorted_before_system_ports(self):
        ports = [
            SimpleNamespace(device="/dev/cu.Bluetooth", description="n/a", vid=None, pid=None, hwid="n/a"),
            SimpleNamespace(device="/dev/cu.usbserial-12", description="USB-RS485 CH340", vid=0x1A86, pid=0x7523, hwid="USB VID:PID=1A86:7523"),
        ]
        module = SimpleNamespace(comports=lambda: ports)
        with patch.dict("sys.modules", {"serial.tools.list_ports": module}):
            result = serial_port_details()
        self.assertTrue(result[0].is_usb)
        self.assertEqual(result[0].device, "/dev/cu.usbserial-12")
        self.assertIn("CH340", result[0].label)
        self.assertFalse(result[1].is_usb)
        self.assertEqual(result[1].description, "未命名设备")

    def test_discovery_does_not_open_a_serial_device(self):
        with patch.dict("sys.modules", {"serial.tools.list_ports": SimpleNamespace(comports=lambda: [])}):
            self.assertEqual(serial_port_details(), [])


if __name__ == "__main__":
    unittest.main()
