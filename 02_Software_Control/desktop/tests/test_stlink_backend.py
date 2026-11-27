import time
import unittest
from unittest.mock import patch

from desktop.stlink_backend import FIELDS, StLinkBackend, WatchLink, stlink_probes


class FakeLink:
    def __init__(self):
        self.data = dict.fromkeys(FIELDS, 0)
        self.data.update({"app.stage": 1, "zdt.position_valid": 1, "zdt.status_valid": 1})
        self.requests = []
        self.verified = False
        self.mismatch = False
        self.instant = True
        self.defer_feedback = False
        self.invalidate_on_move = True
        self.pending_position = None
        self.feedback_offset = 0

    def verify_firmware(self):
        if self.mismatch:
            raise RuntimeError("板上固件与所选 ELF 不一致")
        self.verified = True

    def read(self):
        return self.data.copy()

    def write_request(self, request, fields):
        self.requests.append((request, fields.copy()))
        self.data["app.accepted_requests"] += 1
        self.data["app.active"] = 0
        self.data["app.stage"] = 12
        if request in (11, 12):
            self.data["zdt.status.enabled"] = int(request == 11)
        elif request == 6:
            self.data["zdt.arrived"] = int(self.instant)
            self.data["app.active"] = int(not self.instant)
            if self.instant:
                target = self.data["zdt.position"] + round((-1 if fields["direction"] else 1)*fields["pulses"]*65536/3200)
                if self.defer_feedback:
                    self.pending_position = target+self.feedback_offset
                    if self.invalidate_on_move:
                        self.data["zdt.position_valid"] = 0
                        self.data["zdt.status_valid"] = 0
                else:
                    self.data["zdt.position"] = target
        elif request == 2:
            self.data["zdt.status_valid"] = 1
        elif request == 4:
            if self.pending_position is not None:
                self.data["zdt.position"] = self.pending_position
            self.data["zdt.position_valid"] = 1
            self.data["zdt.position_tick"] += 1
        elif request == 10:
            self.data["app.stage"] = 13
        elif request == 13:
            self.data["zdt.health.valid"] = 1
            self.data["zdt.health.raw"] = 3
        elif request == 20:
            self.data.update({"zdt.home.valid": 1, "zdt.home.mode": 0, "zdt.home.power_on": 1,
                              "zdt.home.speed_rpm": 5, "zdt.home.timeout_ms": 10000, "zdt.position": 0})
        elif request == 7:
            self.data["zdt.voltage_valid"] = 1
            self.data["zdt.bus_voltage_mv"] = 24000


class StLinkTests(unittest.TestCase):
    def setUp(self):
        self.link = FakeLink()
        self.backend = StLinkBackend("ABC123", link_factory=lambda *a: self.link)

    def tearDown(self):
        self.backend.disconnect()
        if self.backend._thread:
            self.backend._thread.join(2)
        self.assertFalse(self.backend._thread and self.backend._thread.is_alive())

    def wait(self, predicate):
        deadline = time.monotonic()+3
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail(str(self.backend.snapshot()))

    def connect(self):
        self.backend.connect()
        self.wait(lambda: self.backend.snapshot().connected)

    def test_discovery_parses_probe_serial_number(self):
        with patch("desktop.stlink_backend.find_tool", return_value="cli"), patch("desktop.stlink_backend.run_tool", return_value="ST-LINK SN : 080046001800004D4B52524E") as run:
            self.assertEqual(stlink_probes()[0].device, "080046001800004D4B52524E")
        self.assertEqual(run.call_args.args[0], ["cli", "-l", "stlink-only"])

    def test_connect_only_reads_and_checks_firmware(self):
        self.connect()
        self.assertTrue(self.link.verified)
        self.assertFalse(self.backend.snapshot().enabled)
        self.assertTrue(all(request in (2, 4, 7, 8) for request, _ in self.link.requests))

    def test_mismatched_firmware_never_writes_requests(self):
        self.link.mismatch = True
        self.backend.connect()
        self.wait(lambda: self.backend.snapshot().error is not None)
        self.assertEqual(self.link.requests, [])
        self.assertFalse(self.backend.snapshot().connected)

    def test_uninitialized_mailbox_never_writes_requests(self):
        self.link.data["request"] = 7077985
        self.backend.connect()
        self.wait(lambda: self.backend.snapshot().error is not None)
        self.assertEqual(self.link.requests, [])
        self.assertIn("main 主循环", self.backend.snapshot().error)

    def test_explicit_enable_and_move_use_watch_mailbox(self):
        self.connect()
        self.backend.enable(True)
        self.wait(lambda: self.backend.snapshot().enabled and self.backend.snapshot().motion == "idle")
        self.backend.move_to(-9, 5)
        self.wait(lambda: self.backend.snapshot().motion == "idle" and self.backend.snapshot().position_deg < -8)
        command = next(fields for request, fields in self.link.requests if request == 6)
        self.assertEqual(command["arm"], 0x454D4D31)
        self.assertEqual(command["pulses"], 80)
        self.assertEqual(command["direction"], 1)
        self.assertEqual(command["mode"], 2)

    def test_ten_degree_step_with_late_feedback_keeps_connection(self):
        self.connect()
        self.backend.enable(True)
        self.wait(lambda: self.backend.snapshot().enabled and self.backend.snapshot().motion == "idle")
        self.link.defer_feedback = True
        self.backend.move_to(10, 5)
        self.wait(lambda: self.backend.snapshot().motion == "confirming")
        self.assertTrue(self.backend.snapshot().connected)
        self.wait(lambda: self.backend.snapshot().motion == "idle")
        self.assertTrue(self.backend.snapshot().connected)
        self.assertIsNone(self.backend.snapshot().error)
        self.assertAlmostEqual(self.backend.snapshot().position_deg, 10.0125, delta=0.006)
        requests = [request for request, _ in self.link.requests]
        self.assertEqual(requests.count(6), 1)
        self.assertNotIn(10, requests)
        move_index = requests.index(6)
        self.assertEqual(requests[move_index+1:move_index+3], [2, 4])

    def test_post_move_refresh_even_when_old_feedback_is_marked_valid(self):
        self.connect()
        self.backend.enable(True)
        self.wait(lambda: self.backend.snapshot().enabled and self.backend.snapshot().motion == "idle")
        self.link.defer_feedback = True
        self.link.invalidate_on_move = False
        self.backend.move_to(10, 5)
        self.wait(lambda: any(request == 6 for request, _ in self.link.requests) and self.backend.snapshot().motion == "idle")
        requests = [request for request, _ in self.link.requests]
        move_index = requests.index(6)
        self.assertEqual(requests[move_index+1:move_index+3], [2, 4])
        self.assertTrue(self.backend.snapshot().connected)

    def test_bad_final_feedback_latches_fault_without_disconnect_or_move_retry(self):
        self.connect()
        self.backend.enable(True)
        self.wait(lambda: self.backend.snapshot().enabled and self.backend.snapshot().motion == "idle")
        self.link.defer_feedback = True
        self.link.feedback_offset = 1000
        self.backend.CONFIRM_TIMEOUT_S = 0.2
        self.backend.move_to(10, 5)
        self.wait(lambda: self.backend.snapshot().motion == "fault")
        self.assertTrue(self.backend.snapshot().connected)
        self.assertIn("实际", self.backend.snapshot().error)
        requests = [request for request, _ in self.link.requests]
        self.assertEqual(requests.count(6), 1)
        self.assertIn(10, requests)
        time.sleep(0.15)
        self.assertTrue(self.backend.snapshot().connected)

    def test_save_startup_origin_is_explicit_and_does_not_move(self):
        self.connect()
        self.backend.save_startup_origin()
        self.wait(lambda: self.backend.snapshot().origin == "固定开机零点" and self.backend.snapshot().motion == "idle")
        requests = [request for request, _ in self.link.requests]
        self.assertEqual(requests.count(20), 1)
        self.assertNotIn(6, requests)
        self.assertNotIn(11, requests)
        fields = next(fields for request, fields in self.link.requests if request == 20)
        self.assertEqual(fields["arm"], 0x484F4D45)
        self.assertEqual(self.backend.snapshot().position_deg, 0)

    def test_disconnect_requests_stop_for_active_motion(self):
        self.connect()
        self.link.instant = False
        self.backend.move_to(90, 5)
        self.wait(lambda: self.backend.snapshot().motion == "positioning")
        self.backend.disconnect()
        self.wait(lambda: not self.backend.snapshot().connected)
        self.assertTrue(any(request == 10 for request, _ in self.link.requests))

    def test_system_bootloader_blocks_connection_before_requests(self):
        link = WatchLink.__new__(WatchLink)
        with patch.object(link, "read_memory", return_value=(0x1FFF0000).to_bytes(4, "little")):
            with self.assertRaisesRegex(RuntimeError, "Bootloader"):
                link.check_application()

    def test_zero_vtor_accepts_flash_mapped_boot_vectors(self):
        link = WatchLink.__new__(WatchLink)
        vectors = (0x20020000).to_bytes(4, "little") + (0x080048D5).to_bytes(4, "little")
        def read(address, size):
            return bytes(4) if address == 0xE000ED08 else vectors
        with patch.object(link, "read_memory", side_effect=read):
            link.check_application()

    def test_zero_vtor_rejects_system_memory_boot_alias(self):
        link = WatchLink.__new__(WatchLink)
        boot_vectors = (0x20001D80).to_bytes(4, "little") + (0x1FFF3DA1).to_bytes(4, "little")
        flash_vectors = (0x20020000).to_bytes(4, "little") + (0x080048D5).to_bytes(4, "little")
        with patch.object(link, "read_memory", side_effect=[bytes(4), boot_vectors, flash_vectors]):
            with self.assertRaisesRegex(RuntimeError, "Bootloader"):
                link.check_application()

    def test_zero_vtor_rejects_invalid_flash_vectors(self):
        link = WatchLink.__new__(WatchLink)
        with patch.object(link, "read_memory", side_effect=[bytes(4), bytes(8), bytes(8)]):
            with self.assertRaisesRegex(RuntimeError, "启动映射"):
                link.check_application()

    def test_explicit_flash_vtor_is_accepted(self):
        link = WatchLink.__new__(WatchLink)
        with patch.object(link, "read_memory", return_value=(0x08000000).to_bytes(4, "little")):
            link.check_application()

    def test_stale_position_cannot_confirm_stop(self):
        self.backend.link = self.link
        self.backend._motion = "stopping"
        self.backend._position_tick = 0
        self.backend._stable_since = time.monotonic()-2
        self.backend._previous = (time.monotonic()-2, 0)
        self.backend._poll()
        self.assertEqual(self.backend.snapshot().motion, "stopping")

    def test_timeout_distinguishes_cleared_mailbox_without_retry(self):
        self.backend.link = self.link
        self.backend._pending = (0, 0)
        self.backend._pending_request = 4
        self.backend._request_time = time.monotonic()-4
        with self.assertRaisesRegex(RuntimeError, "已从邮箱清除"):
            self.backend._read()
        self.assertEqual(self.link.requests, [])

    def test_timeout_distinguishes_request_not_consumed(self):
        self.backend.link = self.link
        self.backend._pending = (0, 0)
        self.backend._pending_request = 4
        self.backend._request_time = time.monotonic()-4
        self.link.data["request"] = 4
        with self.assertRaisesRegex(RuntimeError, "尚未取走请求 4"):
            self.backend._read()
        self.assertEqual(self.link.requests, [])

    def test_watch_writer_publishes_request_last_and_hotplug_only(self):
        link = WatchLink.__new__(WatchLink)
        link.cli = "cli"
        link.serial = "ABC"
        link.base, link.size = 0x20000010, 12
        link.layout = {"request": (0x20000010, 4), "address": (0x20000014, 1), "arm": (0x20000018, 4)}
        with patch("desktop.stlink_backend.run_tool") as run:
            link.write_request(6, {"address": 1, "arm": 0x454D4D31})
        args = run.call_args.args[0]
        self.assertIn("mode=HOTPLUG", args)
        self.assertIn("-nv", args)
        self.assertEqual(args[6:9], ["-w8", "0x20000014", "0x1"])
        self.assertEqual(args[-4:], ["-w32", "0x20000010", "0x6", "-nv"])
        self.assertNotIn("-run", args)
        self.assertNotIn("-rst", args)
        with self.assertRaises(ValueError):
            link.write_request(6, {"zdt.position": 42})
