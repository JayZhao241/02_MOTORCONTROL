import time
import unittest

from desktop.controller import Controller, DemoBackend
from desktop.serial_backend import SerialBackend


class FakeSerial:
    def __init__(self, *args, **kwargs):
        self.frames = []
        self.buffer = bytearray()
        self.enabled = False
        self.position = 0
        self.target = 0
        self.closed = False
        self.silent = False
        self.reject = False
        self.instant = True
        self.home = bytes.fromhex("01 01 00 1e 00 00 27 10 01 2c 03 20 00 3c 00")

    def reset_input_buffer(self):
        self.buffer.clear()

    def write(self, packet):
        self.frames.append(packet)
        address, function = packet[:2]
        data = b""
        if function == 0xF3:
            self.enabled = bool(packet[3])
        elif function == 0xFD and not self.reject:
            pulses = int.from_bytes(packet[6:10], "big")
            self.target = self.position + round((-1 if packet[2] else 1) * pulses * 65536 / 3200)
            if self.instant:
                self.position = self.target
        if function == 0x0A:
            self.position = self.target = 0
        if function == 0x4C:
            self.home = packet[4:19]
        if function >= 0xF0 or function in (0x93, 0x4C, 0x0A):
            data = bytes((0xE2 if self.reject else 0x02,))
        elif function == 0x22:
            data = self.home
        elif function == 0x3B:
            data = b"\x03"
        elif function == 0x1A:
            data = b"\x00\x06"
        elif function == 0x3A:
            data = bytes((int(self.enabled) | (2 if self.position == self.target else 0),))
        elif function in (0x36, 0x34):
            value = self.position if function == 0x36 else self.target
            data = bytes((int(value < 0),)) + abs(value).to_bytes(4, "big")
        elif function == 0x24:
            data = (24000).to_bytes(2, "big")
        elif function == 0x27:
            data = (125).to_bytes(2, "big")
        if not self.silent:
            self.buffer.extend(bytes((address, function)) + data + b"\x6b")
        return len(packet)

    def read(self, size):
        # Deliberately split every reply across multiple reads.
        data = bytes(self.buffer[:2])
        del self.buffer[:2]
        if not data:
            time.sleep(0.002)
        return data

    def close(self):
        self.closed = True


class SerialTests(unittest.TestCase):
    def setUp(self):
        self.serial = FakeSerial()
        self.backend = SerialBackend("test", serial_factory=lambda *a, **kw: self.serial)

    def tearDown(self):
        self.backend.disconnect()
        if self.backend._thread:
            self.backend._thread.join(3)
        self.assertFalse(self.backend._thread and self.backend._thread.is_alive())

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.005)
        self.fail(str(self.backend.snapshot()))

    def connect(self):
        self.backend.connect()
        self.wait_for(lambda: self.backend.snapshot().connected)

    def enable(self):
        self.backend.enable(True)
        self.wait_for(lambda: self.backend.snapshot().enabled and self.backend.snapshot().motion == "idle")

    def test_handshake_reads_feedback_without_enable_or_motion(self):
        self.connect()
        state = self.backend.snapshot()
        self.assertEqual(state.bus_voltage_v, 24)
        self.assertEqual(state.phase_current_a, 0.125)
        self.assertFalse(state.enabled)
        self.assertTrue(all(frame[1] < 0xF0 for frame in self.serial.frames))

    def test_enable_position_frame_and_reverse_origin(self):
        self.connect()
        self.enable()
        self.backend.move_to(9, 5)
        self.wait_for(lambda: any(f[1] == 0xFD for f in self.serial.frames) and self.backend.snapshot().motion == "idle")
        frame = next(f for f in self.serial.frames if f[1] == 0xFD)
        self.assertEqual(frame, bytes.fromhex("01 FD 00 00 05 00 00 00 00 50 02 00 6B"))
        self.backend.set_origin()
        self.wait_for(lambda: self.backend.snapshot().origin == "用户坐标")
        self.assertAlmostEqual(self.backend.snapshot().position_deg, 0)
        self.backend.move_to(-9, 5)
        self.wait_for(lambda: len([f for f in self.serial.frames if f[1] == 0xFD]) == 2 and self.backend.snapshot().motion == "idle")
        self.assertAlmostEqual(self.backend.snapshot().position_deg, -9, delta=0.01)

    def test_continuous_deadline_sends_stop(self):
        self.connect()
        self.enable()
        self.serial.instant = False
        self.backend.rotate(1, 5, 0.1)
        self.wait_for(lambda: any(f == bytes.fromhex("01 FE 98 00 6B") for f in self.serial.frames))
        self.wait_for(lambda: self.backend.snapshot().motion == "idle")
        self.assertIsNone(self.backend.snapshot().remaining_s)

    def test_timeout_invalidates_feedback_and_releases_port(self):
        self.connect()
        self.serial.silent = True
        self.wait_for(lambda: self.backend.snapshot().error is not None)
        self.assertFalse(self.backend.snapshot().connected)
        self.assertTrue(self.serial.closed)
        self.assertIn("超时", self.backend.snapshot().error)

    def test_rejected_move_attempts_stop_without_retry(self):
        self.connect()
        self.enable()
        self.serial.reject = True
        self.backend.move_to(9, 5)
        self.wait_for(lambda: self.backend.snapshot().error is not None)
        self.assertEqual(len([f for f in self.serial.frames if f[1] == 0xFD]), 1)
        self.assertTrue(any(f[1] == 0xFE for f in self.serial.frames))

    def test_switch_requires_disconnect_and_disarms(self):
        controller = Controller()
        controller.connect()
        with self.assertRaises(ValueError):
            controller.select_backend(self.backend)
        controller.disconnect()
        controller.armed = (1, 5, 10)
        controller.select_backend(self.backend)
        self.assertIsNone(controller.armed)
        self.assertIs(controller.backend, self.backend)
        controller.select_backend(DemoBackend())

    def test_persistent_origin_preserves_homing_limits_and_does_not_move(self):
        old = self.serial.home
        self.connect()
        self.backend.save_startup_origin()
        self.wait_for(lambda: self.backend.snapshot().origin == "固定开机零点")
        self.assertEqual(self.serial.home[0], 0)
        self.assertEqual(int.from_bytes(self.serial.home[2:4], "big"), 5)
        self.assertEqual(self.serial.home[8:14], old[8:14])
        self.assertEqual(self.serial.home[14], 1)
        self.assertTrue(any(frame == bytes.fromhex("01 93 88 01 6b") for frame in self.serial.frames))
        self.assertFalse(any(frame[1] in (0xFD, 0x9A, 0xF3) for frame in self.serial.frames))

    def test_invalid_connection_settings(self):
        for args in (("",), ("COM1", 0), ("COM1", 115200, 0), ("COM1", 115200, 1, 1.5)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                SerialBackend(*args)

    def test_disconnect_stops_active_motion_and_closes(self):
        self.connect()
        self.enable()
        self.serial.instant = False
        self.backend.move_to(90, 5)
        self.wait_for(lambda: self.backend.snapshot().motion == "positioning")
        self.backend.disconnect()
        self.wait_for(lambda: self.serial.closed)
        self.assertTrue(any(f[1] == 0xFE for f in self.serial.frames))
        self.assertFalse(self.backend.snapshot().connected)


if __name__ == "__main__":
    unittest.main()
