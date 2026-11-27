import time
import unittest
from desktop.controller import Controller
from desktop.dual_backend import DualDemoBackend, DualSerialBackend
from desktop.pose_control import PoseControl
from test_serial_backend import FakeSerial


class Bus:
    def __init__(self):
        self.devices = {1:FakeSerial(),2:FakeSerial()}
        self.frames = []
        self.buffer = bytearray()
        self.closed = False
        self.cached = {}
        self.fail_second = False
        self.response_modes = {1:1, 2:1}
        self.drop_after_stop = 0
        self.stop_seen = False

    def reset_input_buffer(self):
        self.buffer.clear()

    def write(self, packet):
        self.frames.append(packet)
        if packet[1] == 0xFE:
            self.stop_seen = True
        if packet[0] == 1 and packet[1] == 0x3A and self.stop_seen and self.drop_after_stop:
            self.drop_after_stop -= 1
            return len(packet)
        if packet[1] == 0x42:
            config = bytearray.fromhex("01 42 21 15 19 02 02 02 00 10 01 00 04 B0 0B 80 0F A0 05 07 01 00 01 01 00 08 08 98 07 D0 00 08 6B")
            config[0] = packet[0]
            config[22] = self.response_modes[packet[0]]
            self.buffer.extend(config)
        elif packet == bytes.fromhex('00 FF 66 6B'):
            for address,frame in self.cached.items():
                self.devices[address].write(frame)
                self.devices[address].buffer.clear()
            self.cached.clear()
        elif packet[1] == 0xFD and packet[11] == 1:
            if self.fail_second and packet[0] == 2:
                self.buffer.extend(bytes((2,0xFD,0xE2,0x6B)))
            else:
                self.cached[packet[0]] = packet
                self.buffer.extend(bytes((packet[0],0xFD,2,0x6B)))
        else:
            dev = self.devices[packet[0]]
            if packet[1] == 0xFE:
                self.cached.pop(packet[0], None)
            dev.write(packet)
            self.buffer.extend(dev.buffer)
            dev.buffer.clear()
        return len(packet)

    def read(self, count):
        data = bytes(self.buffer[:count]); del self.buffer[:count]
        return data

    def close(self):
        self.closed = True


class DualTests(unittest.TestCase):
    def wait_for(self, predicate):
        end = time.monotonic()+4
        while time.monotonic()<end:
            if predicate(): return
            time.sleep(.01)
        self.fail(str([a.snapshot() for a in self.backend.axes]))

    def setUp(self):
        self.bus = Bus()
        self.backend = DualSerialBackend('test',serial_factory=lambda *a,**k:self.bus)
        self.backend.connect()
        self.wait_for(lambda:all(a.snapshot().connected for a in self.backend.axes))

    def tearDown(self):
        self.backend.disconnect()
        self.backend._thread.join(4)
        self.assertFalse(self.backend._thread.is_alive())

    def enable(self):
        self.backend.enable_all()
        self.wait_for(lambda:all(a.snapshot().enabled and a.snapshot().motion=='idle' for a in self.backend.axes))

    def test_connect_reads_both_without_motion(self):
        self.assertEqual({f[0] for f in self.bus.frames},{1,2})
        self.assertTrue(all(f[1]<0xF0 for f in self.bus.frames))

    def test_independent_axis_and_shared_transport(self):
        self.enable(); self.backend.selected=1
        self.backend.move_to(9,1)
        self.wait_for(lambda:abs(self.backend.axes[1].snapshot().position_deg-9)<.01)
        self.assertEqual(self.backend.axes[0].snapshot().position_deg,0)
        self.assertEqual([f[0] for f in self.bus.frames if f[1]==0xFD],[2])

    def test_sync_trigger_follows_both_cached_targets(self):
        self.enable(); self.backend.move_pair((9,-9),1)
        self.wait_for(lambda:all(a.snapshot().motion=='idle' for a in self.backend.axes))
        frames=[f for f in self.bus.frames if f[1] in (0xFD,0xFF)]
        self.assertEqual([f[0] for f in frames],[1,2,0])
        self.assertTrue(all(f[11]==1 for f in frames[:2]))
        self.assertAlmostEqual(self.backend.axes[1].snapshot().position_deg,-9,delta=.01)

    def test_continuous_deadline_with_one_missing_status_stays_connected(self):
        self.enable()
        self.bus.devices[1].instant=False
        self.bus.stop_seen=False
        self.bus.drop_after_stop=1
        self.backend.rotate(1,5,0.2)
        self.wait_for(lambda:any(f[1]==0xFD for f in self.bus.frames))
        self.wait_for(lambda:all(a.snapshot().motion=="idle" for a in self.backend.axes))
        self.assertTrue(all(a.snapshot().connected for a in self.backend.axes))
        self.assertIsNone(self.backend.snapshot().error)
        self.assertIsNone(self.backend.snapshot().remaining_s)
        self.assertEqual(len([f for f in self.bus.frames if f[1]==0xFD]),1)
        self.assertEqual([f[0] for f in self.bus.frames if f[1]==0xFE],[1,2])
        self.assertEqual(self.bus.drop_after_stop,0)

    def test_nearest_zero_sends_short_move_to_selected_motor_only(self):
        self.enable()
        self.bus.devices[2].position = self.bus.devices[2].target = round(730*65536/360)
        self.wait_for(lambda:abs(self.backend.axes[1].snapshot().position_deg-730)<.01)
        self.backend.selected=1
        controller=Controller(self.backend)
        controller.return_zero(5)
        self.wait_for(lambda:abs(self.backend.axes[1].snapshot().position_deg-720)<.12)
        moves=[f for f in self.bus.frames if f[1]==0xFD]
        self.assertEqual(len(moves),1)
        self.assertEqual(moves[0][0],2)
        self.assertEqual(moves[0][2],1)
        self.assertLessEqual(int.from_bytes(moves[0][6:10],"big"),1600)
        self.assertFalse(any(f[1] in (0x0A,0x93,0x4C) for f in self.bus.frames))

    def test_active_arrival_reply_is_rejected_before_caching_either_axis(self):
        self.enable()
        self.bus.response_modes[2] = 3
        self.backend.move_pair((9,9),1)
        self.wait_for(lambda: self.bus.closed)
        self.assertFalse(any(f[1] in (0xFD,0xFF,0x48) for f in self.bus.frames))
        self.assertIn("Response=Both",self.backend.snapshot().error)
        self.assertIn("Receive",self.backend.snapshot().error)
        self.assertTrue(any(f == bytes.fromhex("02 42 6C 6B") for f in self.bus.frames))

    def test_second_rejection_stops_both_and_never_triggers(self):
        self.enable(); self.bus.fail_second=True
        self.backend.move_pair((9,9),1)
        self.wait_for(lambda: self.bus.closed)
        self.assertFalse(any(f[1]==0xFF for f in self.bus.frames))
        self.assertEqual({f[0] for f in self.bus.frames if f[1]==0xFE},{1,2})
        self.assertFalse(self.bus.cached)

    def test_stop_frames_are_sent_to_both_axes_before_feedback_reads(self):
        self.enable()
        # Stop the worker first; exercise only the ordering of the bus operation.
        self.backend.disconnect(); self.backend._thread.join(4)
        self.bus.closed = False
        self.bus.frames.clear()
        self.backend._stop_both()
        self.assertEqual([f[:2] for f in self.bus.frames], [bytes((1,0xFE)), bytes((2,0xFE))])

    def test_queued_operator_request_preempts_background_poll(self):
        self.enable()
        self.backend.disconnect(); self.backend._thread.join(4)
        self.bus.closed = False
        axis = self.backend.axes[0]
        self.backend._commands.put((0,"stop",()))
        self.bus.frames.clear()
        axis._poll()
        self.assertEqual([f[1] for f in self.bus.frames],[0x3A])
        self.backend._commands.get_nowait()


class PoseTests(unittest.TestCase):
    def setUp(self):
        self.backend=DualDemoBackend(); self.backend.connect(); self.backend.enable_all()
        self.pose=PoseControl(self.backend)

    def test_requires_center_and_checks_configured_limits(self):
        with self.assertRaises(ValueError): self.pose.move(1,0,1)
        self.pose.calibrate(1,-1)
        self.pose.configure_limits(40,12)
        for values in ((41,0,1),(0,13,1),(0,0,0)):
            with self.assertRaises(ValueError): self.pose.move(*values)
        self.pose.move(30,8,5)
        for _ in range(100): self.backend.update(.1); self.pose.update()
        self.assertEqual(self.pose.coordinates(),(30,8))

    def test_example_angles_are_not_mechanical_limits(self):
        self.pose.calibrate(1,1)
        self.pose.move(32,9,5)
        for _ in range(100): self.backend.update(.1); self.pose.update()
        self.assertEqual(self.pose.coordinates(),(32,9))
        self.pose.sweep('beta',8,30,5,1)
        for _ in range(100): self.backend.update(.1); self.pose.update()
        self.assertEqual(self.pose.coordinates(),(30,0))

    def test_limits_are_atomic_optional_and_apply_to_scan(self):
        self.pose.calibrate(1,1)
        self.pose.configure_limits(40,10)
        with self.assertRaises(ValueError): self.pose.configure_limits(50,'nan')
        self.assertEqual(self.pose.limits,(40,10))
        with self.assertRaises(ValueError): self.pose.sweep('beta',11,0,1,1)
        with self.assertRaises(ValueError): self.pose.sweep('alpha',5,11,1,1)
        self.assertFalse(self.pose.sequence)
        self.pose.move(5,0,1)
        with self.assertRaises(ValueError): self.pose.configure_limits()
        self.pose.stop(); self.pose.configure_limits()
        self.assertIsNone(self.pose.limits)

    def test_sign_center_mapping_and_feedback(self):
        self.backend.axes[0].position=10; self.backend.axes[1].position=20
        self.pose.calibrate(1,-1); self.pose.move(5,0,1)
        self.backend.update(2)
        self.assertEqual([a.position for a in self.backend.axes],[15,15])
        self.assertEqual(self.pose.coordinates(),(5,0))
        self.pose.move(5,2,1); self.backend.update(2)
        self.assertEqual([a.position for a in self.backend.axes],[17,17])
        self.assertEqual(self.pose.coordinates(),(5,2))

    def test_combined_pose_uses_two_segments_within_workspace(self):
        self.pose.calibrate(1,1); self.pose.move(25,5,1)
        for _ in range(200):
            self.backend.update(.1); self.pose.update()
            a,b=self.pose.coordinates()
            self.assertLessEqual(abs(a),25); self.assertLessEqual(abs(b),5)
        self.assertEqual(self.pose.coordinates(),(25,5))

    def test_reciprocation_finishes_and_stop_clears_future_targets(self):
        self.pose.calibrate(1,1); self.pose.sweep('alpha',5,0,1,2)
        for _ in range(200): self.backend.update(.1); self.pose.update()
        self.assertFalse(self.pose.sequence)
        self.assertEqual(self.pose.coordinates(),(0,0))
        self.pose.sweep('beta',2,0,1,2); self.pose.stop()
        self.backend.update(10); self.pose.update()
        self.assertFalse(self.pose.sequence)
        self.assertTrue(all(a.snapshot().motion=='idle' for a in self.backend.axes))
