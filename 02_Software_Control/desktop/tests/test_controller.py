import math
import unittest

from desktop.controller import Controller, firmware_default_rpm


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.motor = Controller()
        self.motor.connect()
        self.motor.enable(True)

    def continuous(self):
        self.motor.change_mode("continuous")

    def test_default_matches_current_firmware(self):
        self.assertEqual(firmware_default_rpm(), 5)

    def test_connect_does_not_enable_or_move(self):
        self.motor.disconnect()
        self.motor.connect()
        self.assertFalse(self.motor.snapshot().enabled)
        self.assertEqual(self.motor.snapshot().motion, "idle")
        with self.assertRaises(ValueError):
            self.motor.jog(1, 1, 5)

    def test_single_jog_does_not_repeat(self):
        self.motor.jog(1, "9", "5")
        self.motor.update(1)
        self.motor.update(10)
        self.assertEqual(self.motor.snapshot().position_deg, 9)
        self.assertEqual(self.motor.snapshot().motion, "idle")

    def test_busy_jog_does_not_queue_or_replace_target(self):
        self.motor.jog(1, 90, 5)
        with self.assertRaises(ValueError):
            self.motor.jog(-1, 10, 5)
        self.assertEqual(self.motor.snapshot().target_deg, 90)
        self.motor.update(20)
        self.assertEqual(self.motor.snapshot().position_deg, 90)

    def test_reverse_and_absolute_and_zero(self):
        self.motor.jog(-1, 15, 5)
        self.motor.update(1)
        self.assertEqual(self.motor.snapshot().position_deg, -15)
        self.motor.move_to(450, 5)
        self.motor.update(20)
        self.assertEqual(self.motor.snapshot().position_deg, 450)
        self.motor.return_zero(5)
        self.motor.update(20)
        self.assertEqual(self.motor.snapshot().position_deg, 0)

    def test_setting_origin_changes_coordinate_without_motion(self):
        self.motor.move_to(90, 5)
        self.motor.update(3)
        self.motor.set_origin()
        self.assertEqual(self.motor.snapshot().position_deg, 0)
        self.assertEqual(self.motor.snapshot().motion, "idle")
        self.motor.jog(1, 5, 5)
        with self.assertRaises(ValueError):
            self.motor.set_origin()

    def test_continuous_requires_direction_and_lock(self):
        self.continuous()
        with self.assertRaises(ValueError):
            self.motor.arm(0, 5, 10)
        with self.assertRaises(ValueError):
            self.motor.rotate(1, 5, 10)
        self.assertEqual(self.motor.snapshot().motion, "idle")

    def test_changed_parameters_invalidate_and_consume_lock(self):
        self.continuous()
        for params in ((-1, 5, 10), (1, 6, 10), (1, 5, 11)):
            self.motor.arm(1, 5, 10)
            with self.assertRaises(ValueError):
                self.motor.rotate(*params)
            self.assertIsNone(self.motor.armed)
            self.assertEqual(self.motor.snapshot().motion, "idle")

    def test_continuous_times_out_without_overshoot(self):
        self.continuous()
        self.motor.arm(-1, 5, 2)
        self.motor.rotate(-1, 5, 2)
        self.assertIsNone(self.motor.armed)
        self.motor.update(0.5)
        self.assertAlmostEqual(self.motor.snapshot().remaining_s, 1.5)
        self.motor.update(100)
        self.assertAlmostEqual(self.motor.snapshot().position_deg, -60)
        self.assertEqual(self.motor.snapshot().motion, "idle")
        self.motor.update(10)
        self.assertAlmostEqual(self.motor.snapshot().position_deg, -60)
        with self.assertRaises(ValueError):
            self.motor.rotate(-1, 5, 2)

    def test_stop_mode_change_disconnect_and_disable_stop_motion(self):
        for action in (lambda: self.motor.stop(), lambda: self.motor.change_mode("position"),
                       lambda: self.motor.disconnect(), lambda: self.motor.enable(False)):
            self.motor.connect()
            self.motor.enable(True)
            self.continuous()
            self.motor.arm(1, 5, 10)
            self.motor.rotate(1, 5, 10)
            self.motor.update(0.5)
            position = self.motor.snapshot().position_deg
            action()
            self.motor.update(10)
            self.assertEqual(self.motor.snapshot().position_deg, position)
            self.assertEqual(self.motor.snapshot().motion, "idle")
            self.assertIsNone(self.motor.armed)

    def test_stop_disconnect_disable_and_mode_change_disarm(self):
        for action in (lambda: self.motor.stop(), lambda: self.motor.disconnect(),
                       lambda: self.motor.enable(False), lambda: self.motor.change_mode("position")):
            self.motor.connect()
            self.motor.enable(True)
            self.continuous()
            self.motor.arm(1, 5, 10)
            action()
            self.assertIsNone(self.motor.armed)

    def test_invalid_angles_do_not_start(self):
        for value in ("", "abc", "nan", "inf", "-inf", 360001, -360001, math.nan):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.motor.move_to(value, 5)
        self.assertEqual(self.motor.snapshot().motion, "idle")

    def test_invalid_step_and_speed_do_not_start(self):
        for value in (0, -1, 36001, "NaN", "Inf"):
            with self.subTest(step=value), self.assertRaises(ValueError):
                self.motor.jog(1, value, 5)
        for value in (0, -1, 3001, "NaN", "Inf", 5.5):
            with self.subTest(rpm=value), self.assertRaises(ValueError):
                self.motor.move_to(90, value)
        self.assertEqual(self.motor.snapshot().motion, "idle")

    def test_invalid_duration_does_not_arm(self):
        self.continuous()
        for value in (0, -1, 61, "NaN", "Inf", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.motor.arm(1, 5, value)
        self.assertIsNone(self.motor.armed)

    def test_mode_is_enforced(self):
        with self.assertRaises(ValueError):
            self.motor.arm(1, 5, 1)
        self.continuous()
        with self.assertRaises(ValueError):
            self.motor.move_to(90, 5)

    def test_position_bounds_apply_to_relative_moves(self):
        self.motor.move_to(360000, 3000)
        self.motor.update(100)
        with self.assertRaises(ValueError):
            self.motor.jog(1, 1, 5)


if __name__ == "__main__":
    unittest.main()
