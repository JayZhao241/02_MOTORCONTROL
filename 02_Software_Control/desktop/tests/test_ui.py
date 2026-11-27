"""Real Tk widget checks; requires an interactive desktop, never opens hardware."""
import tkinter as tk
from dataclasses import replace
import unittest
from unittest.mock import patch

from desktop.app import MotorApp
from desktop.serial_backend import SerialPortInfo


class UITests(unittest.TestCase):
    def setUp(self):
        self.app = MotorApp()
        self.app.update()

    def tearDown(self):
        self.app._close()

    def connect_enabled(self):
        self.app.connect_button.invoke()
        self.app.enable_button.invoke()

    def continuous(self):
        self.app.tabs.select(1)
        self.app.update()

    def test_connect_and_single_click_and_busy_button(self):
        self.assertTrue(self.app.forward_button.instate(["disabled"]))
        self.connect_enabled()
        self.app.step.set("90")
        self.app.forward_button.invoke()
        self.assertEqual(self.app.controller.snapshot().target_deg, -90)
        self.assertTrue(self.app.forward_button.instate(["disabled"]))
        self.app.reverse_button.invoke()
        self.assertEqual(self.app.controller.snapshot().target_deg, -90)
        self.app._stop()
        self.assertEqual(self.app.controller.snapshot().motion, "idle")

    def test_clockwise_and_counterclockwise_steps_follow_motor_direction(self):
        self.connect_enabled()
        self.app.step.set("10")
        self.app.forward_button.invoke()
        self.assertEqual(self.app.controller.snapshot().target_deg, -10)
        self.assertIn("顺时针一步", self.app.notice.get())
        self.app._stop()
        position = self.app.controller.snapshot().position_deg
        self.app.reverse_button.invoke()
        self.assertAlmostEqual(self.app.controller.snapshot().target_deg, position+10)
        self.assertIn("逆时针一步", self.app.notice.get())

    def test_continuous_clockwise_and_counterclockwise_use_same_mapping(self):
        self.connect_enabled()
        self.continuous()
        for index, direction in ((0, -1), (1, 1)):
            self.app.direction_radios[index].invoke()
            self.app.lock_check.invoke()
            self.app.run_button.invoke()
            self.assertEqual(self.app.controller.snapshot().motion, "continuous")
            self.assertEqual(self.app.controller.backend.direction, direction)
            self.app._stop()

    def test_lock_direction_parameter_changes_and_mode_switch(self):
        self.connect_enabled()
        self.continuous()
        self.app.lock_check.invoke()  # Missing direction: reject and uncheck.
        self.assertFalse(self.app.unlock.get())
        self.assertTrue(self.app.run_button.instate(["disabled"]))
        self.app.direction.set(1)
        self.app.lock_check.invoke()
        self.assertTrue(self.app.unlock.get())
        self.app.continuous_rpm.set("10")
        self.assertFalse(self.app.unlock.get())
        self.assertTrue(self.app.run_button.instate(["disabled"]))
        self.app.lock_check.invoke()
        self.app.run_button.invoke()
        self.assertEqual(self.app.controller.snapshot().motion, "continuous")
        self.assertFalse(self.app.unlock.get())
        self.app.tabs.select(0)
        self.app.update()
        self.assertEqual(self.app.controller.snapshot().motion, "idle")
        self.assertIsNone(self.app.controller.armed)

    def test_invalid_input_is_visible_and_defaults_are_independent(self):
        self.connect_enabled()
        self.app.step.set("NaN")
        self.app.forward_button.invoke()
        self.assertEqual(self.app.controller.snapshot().motion, "idle")
        self.assertIn("范围", self.app.notice.get())
        self.app.position_rpm.set("10")
        self.app.continuous_rpm.set("20")
        self.app._default(self.app.position_rpm)
        self.assertEqual(self.app.position_rpm.get(), "5")
        self.assertEqual(self.app.continuous_rpm.get(), "20")

    def test_stop_escape_and_disconnect_are_always_available(self):
        self.app._stop()
        self.connect_enabled()
        self.app.step.set("90")
        self.app.forward_button.invoke()
        self.app.focus_force()
        self.app.event_generate("<Escape>")
        self.app.update()
        self.assertEqual(self.app.controller.snapshot().motion, "idle")
        self.app.forward_button.invoke()
        self.app.connect_button.invoke()
        self.assertFalse(self.app.controller.snapshot().connected)
        self.assertEqual(self.app.angle.get(), "—")

    def test_connection_options_and_serial_settings(self):
        self.app.connection_mode.set("串口连接")
        with patch("desktop.app.serial_port_details", return_value=[SerialPortInfo("/dev/cu.test", "USB-RS485", True)]):
            self.app._connection_changed()
        self.app.update_idletasks()
        self.assertTrue(self.app.serial_settings.winfo_viewable())
        self.assertEqual(self.app.port.get(), "/dev/cu.test")
        self.app.connection_mode.set("模拟连接")
        self.app._connection_changed()
        self.connect_enabled()
        self.assertTrue(self.app.mode_picker.instate(["disabled"]))
        self.app._connect()
        self.assertTrue(self.app.mode_picker.instate(["readonly"]))

    def test_invalid_real_connection_does_not_fall_back(self):
        self.app.connection_mode.set("串口连接")
        self.app._connection_changed()
        self.app.port.set("")
        self.app._connect()
        self.assertFalse(self.app.controller.snapshot().connected)
        self.assertIn("串口", self.app.notice.get())
        self.assertEqual(self.app.connection_mode.get(), "串口连接")

    def test_serial_controls_are_visible_in_simulation(self):
        self.assertTrue(self.app.port_picker.winfo_viewable())
        self.assertTrue(self.app.port_picker.instate(["disabled"]))
        self.assertIn("连接方式", self.app.port_hint.get())

    def test_system_ports_are_not_automatically_selected(self):
        self.app.connection_mode.set("串口连接")
        with patch("desktop.app.serial_port_details", return_value=[SerialPortInfo("/dev/cu.Bluetooth-Incoming-Port", "蓝牙")]):
            self.app._connection_changed()
        self.assertEqual(self.app.port.get(), "")
        self.assertIn("未发现 USB", self.app.port_hint.get())
        self.assertTrue(self.app.port_picker.instate(["!disabled"]))

    def test_usb_labels_use_raw_path_for_connection_and_clear_on_unplug(self):
        self.app.connection_mode.set("串口连接")
        usb = SerialPortInfo("/dev/cu.usbserial-12345678", "CH340 USB-RS485", True)
        with patch("desktop.app.serial_port_details", return_value=[usb]):
            self.app._connection_changed()
        self.assertIn("CH340", self.app.port_choice.get())
        self.assertEqual(self.app.port.get(), usb.device)
        with patch("desktop.app.DualSerialBackend", side_effect=ValueError("测试连接")) as factory:
            self.app._connect()
            factory.assert_called_once_with(usb.device, "115200", "3200")
        with patch("desktop.app.serial_port_details", return_value=[]):
            self.app._scan_ports()
        self.assertEqual(self.app.port.get(), "")
        self.assertEqual(self.app.port_choice.get(), "")

    def test_multiple_usb_ports_require_selection_and_keep_manual_path(self):
        self.app.connection_mode.set("串口连接")
        usb = [SerialPortInfo("COM3", "CH340", True), SerialPortInfo("COM4", "FTDI", True)]
        with patch("desktop.app.serial_port_details", return_value=usb):
            self.app._connection_changed()
            self.assertEqual(self.app.port.get(), "")
            self.app.port_choice.set(usb[1].label)
            self.assertEqual(self.app.port.get(), "COM4")
            self.app._scan_ports()
            self.assertEqual(self.app.port.get(), "COM4")
            self.app.port_choice.set("COM9")
            self.app._scan_ports()
            self.assertEqual(self.app.port.get(), "COM9")

    def test_real_connection_layout_at_800_height(self):
        self.app.geometry("1060x800")
        self.app.connection_mode.set("串口连接")
        with patch("desktop.app.serial_port_details", return_value=[]):
            self.app._connection_changed()
        self.app.update()
        for mode in (0, 1):
            self.app.tabs.select(mode)
            self.app.update()
            for widget in (self.app.port_picker, self.app.logs, self.app.zero_button if mode == 0 else self.app.run_button):
                self.assertTrue(widget.winfo_viewable(), str(widget))
                self.assertGreater(widget.winfo_height(), 10)
                ancestor = widget.master
                while ancestor is not None:
                    self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), ancestor.winfo_rooty() + ancestor.winfo_height())
                    ancestor = ancestor.master

    def test_stlink_uses_probe_selection_and_hides_baudrate(self):
        from desktop.stlink_backend import ProbeInfo
        self.app.connection_mode.set("ST-Link 连接")
        with patch("desktop.app.stlink_probes", return_value=[ProbeInfo("ABC123")]):
            self.app._connection_changed()
            import time
            deadline = time.monotonic()+2
            while self.app.probe_results.empty() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.app._tick()
        self.app.update()
        self.assertEqual(self.app.port.get(), "ABC123")
        self.assertTrue(self.app.elf_button.winfo_viewable())
        self.assertFalse(self.app.baud_widgets[1].winfo_viewable())
        self.assertIn("ST-Link", self.app.device_label.get())
        with patch("desktop.app.DualSerialBackend", side_effect=ValueError("测试连接")) as factory:
            self.app._connect()
            self.assertEqual(factory.call_args.args, ("ABC123",))
            self.assertEqual(factory.call_args.kwargs["pulses_per_turn"], "3200")

    def test_confirmation_keeps_connection_and_blocks_another_jog(self):
        state = replace(self.app.controller.snapshot(), connected=True, enabled=True, motion="confirming")
        with patch.object(self.app.controller.backend, "snapshot", return_value=state):
            self.app._refresh()
        self.assertEqual(self.app.connection.get(), "已连接")
        self.assertEqual(self.app.info["motion"].get(), "确认到位中")
        self.assertTrue(self.app.forward_button.instate(["disabled"]))

    def test_final_feedback_fault_stays_connected(self):
        state = replace(self.app.controller.snapshot(), connected=True, enabled=True, motion="fault", error="位置反馈未到目标")
        with patch.object(self.app.controller.backend, "snapshot", return_value=state):
            self.app._refresh()
        self.assertEqual(self.app.connection.get(), "已连接")
        self.assertEqual(self.app.info["motion"].get(), "位置反馈异常")
        self.assertTrue(self.app.forward_button.instate(["disabled"]))

    def test_save_startup_origin_button_is_explicit(self):
        self.connect_enabled()
        self.app.controller.backend.axes[0].position = 25
        self.assertEqual(self.app.origin_button.cget("text"), "保存为开机零点")
        self.app.origin_button.invoke()
        self.app._advance()
        self.assertEqual(self.app.controller.snapshot().position_deg, 0)
        self.assertEqual(self.app.controller.snapshot().origin, "模拟开机零点")
        self.assertEqual(self.app.notice.get(), "开机零点已保存")

    def test_layout_at_minimum_window_size(self):
        self.app.geometry("1060x950")
        self.app.update()
        for widget in (self.app.zero_button, self.app.origin_button, self.app.logs, self.app.notice_label):
            self.assertTrue(widget.winfo_viewable())
            self.assertGreater(widget.winfo_height(), 10)
            ancestor = widget.master
            while ancestor is not None:
                self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(),
                                     ancestor.winfo_rooty() + ancestor.winfo_height())
                ancestor = ancestor.master
        self.continuous()
        self.assertTrue(self.app.run_button.winfo_viewable())

    def test_visible_text_is_not_clipped_at_minimum_size(self):
        self.app.geometry("1060x950")
        self.connect_enabled()
        for mode in (0, 1):
            self.app.tabs.select(mode)
            self.app.update()
            widgets = list(self.app.winfo_children())
            while widgets:
                widget = widgets.pop()
                widgets.extend(widget.winfo_children())
                if widget.winfo_class() != "TLabel" or not widget.winfo_viewable():
                    continue
                self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight(), str(widget))
                ancestor = widget.master
                while ancestor is not None:
                    self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(),
                                         ancestor.winfo_rooty() + ancestor.winfo_height(), str(widget))
                    ancestor = ancestor.master

    def test_axis_selection_and_pose_scan(self):
        self.connect_enabled()
        self.app.motor_picker.current(1)
        self.app._motor_changed()
        self.app._enable()
        self.assertTrue(self.app.controller.backend.axes[1].snapshot().enabled)
        self.app.workspaces.select(1)
        self.app.update()
        self.app._calibrate_pose()
        self.assertIsNotNone(self.app.pose.center)
        self.app.pose.move(5, 0, 1)
        self.app.controller.update(2)
        self.assertEqual(self.app.pose.coordinates(), (5, 0))
        self.app.pose.sweep("beta", 2, 5, 1, 2)
        self.app._stop()
        self.assertFalse(self.app.pose.sequence)
        self.assertTrue(all(a.snapshot().motion == "idle" for a in self.app.controller.backend.axes))

    def test_pose_controls_fit_800_height(self):
        self.app.geometry("1060x800")
        self.app.workspaces.select(1)
        for page, widgets in ((0, (self.app.pose_move_button, self.app.pose_zero_button)),
                              (1, (self.app.pose_scan_button,)),
                              (2, (self.app.calibrate_button, self.app.apply_limits_button))):
            self.app.pose_tabs.select(page)
            self.app.update()
            for widget in (*widgets, self.app.enable_all_button):
                self.assertTrue(widget.winfo_viewable(), str(widget))
                self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight(), str(widget))
                ancestor = widget.master
                while ancestor is not None:
                    self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(), ancestor.winfo_rooty()+ancestor.winfo_height(), str(widget))
                    ancestor = ancestor.master

    def test_single_and_dual_controls_are_separate(self):
        self.assertEqual(len(self.app.tabs.tabs()), 2)
        self.assertEqual(len(self.app.workspaces.tabs()), 2)
        self.app.workspaces.select(1)
        self.app.update()
        self.assertFalse(self.app.motor_picker.winfo_viewable())
        self.assertFalse(self.app.enable_button.winfo_viewable())
        self.assertTrue(self.app.enable_all_button.winfo_viewable())
        self.assertTrue(self.app.dual_feedback.winfo_viewable())
        self.assertFalse(self.app.single_feedback.winfo_viewable())
        self.app.workspaces.select(0)
        self.app.update()
        self.assertTrue(self.app.motor_picker.winfo_viewable())
        self.assertTrue(self.app.single_feedback.winfo_viewable())

    def test_parameter_hints_and_experimental_ranges(self):
        self.app.alpha.set("30")
        self.app.beta.set("8")
        self.assertIn("+38°", self.app.pose_preview.get())
        self.assertIn("+22°", self.app.pose_preview.get())
        self.app.sweep_axis.set("俯仰 β")
        self.assertEqual(self.app.fixed_label.get(), "固定扇扫 α")
        self.assertIsNone(self.app.pose.limits)
        self.app.use_limits.set(True)
        self.app.alpha_limit.set("42")
        self.app.beta_limit.set("12")
        self.app._apply_limits()
        self.assertEqual(self.app.pose.limits, (42, 12))
        self.app.alpha_limit.set("0")
        self.app._apply_limits()
        self.assertEqual(self.app.pose.limits, (42, 12))
        self.app.use_limits.set(False)
        self.app._apply_limits()
        self.assertIsNone(self.app.pose.limits)

    def test_offline_pose_does_not_display_stale_feedback(self):
        self.connect_enabled()
        self.app.controller.backend.enable_all()
        self.app.pose.calibrate(1, 1)
        self.app.controller.backend.axes[0].position = 10
        self.app.controller.backend.axes[1].position = 10
        self.app.controller.disconnect()
        self.app._refresh()
        self.assertIn("离线", self.app.pose_feedback.get())
        self.assertNotIn("α=", self.app.pose_feedback.get())

    def test_busy_action_has_immediate_progress_and_edit_lock(self):
        self.connect_enabled()
        self.app.step.set("90")
        self.app.forward_button.invoke()
        self.assertIsNotNone(self.app._busy_since)
        self.assertIn("已用",self.app.elapsed_text.get())
        self.assertTrue(all(entry.instate(["disabled"]) for entry in self.app.motion_inputs))
        self.assertIn(str(self.app.progress.cget("mode")), ("indeterminate","determinate"))
        self.app._stop()
        self.assertIsNone(self.app._busy_since)
        self.assertTrue(all(entry.instate(["!disabled"]) for entry in self.app.motion_inputs))

    def test_connection_stage_is_visible_without_fake_percent(self):
        states = [replace(a.snapshot(), motion="connecting") for a in self.app.controller.backend.axes]
        self.app._activity(states,True,False)
        self.assertEqual(str(self.app.progress.cget("mode")),"indeterminate")
        self.assertIn("已用",self.app.elapsed_text.get())

    def test_workflow_names_current_single_motor(self):
        self.connect_enabled()
        self.app.motor_picker.current(1); self.app._motor_changed(); self.app._enable()
        self.assertIn("ID 2", self.app.workflow_text.get())

    def test_nearest_zero_shows_single_turn_zero_and_keeps_turn_count(self):
        self.connect_enabled()
        self.app.controller.backend.axes[0].position = 730
        self.app.zero_button.invoke()
        self.assertEqual(self.app.controller.snapshot().target_deg,720)
        self.app.controller.update(1)
        self.app._refresh()
        self.assertEqual(self.app.angle.get(),"0.000°")
        self.assertIn("+720.000°",self.app.angle_caption.get())
        self.assertEqual(self.app.controller.backend.axes[1].position,0)
        self.assertEqual(self.app.zero_button.cget("text"),"就近回零")


if __name__ == "__main__":
    unittest.main()
