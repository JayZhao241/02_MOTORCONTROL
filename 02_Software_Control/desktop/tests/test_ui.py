"""Real Tk widget checks; requires an interactive desktop, never opens hardware."""
import tkinter as tk
import unittest

from desktop.app import MotorApp


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
        self.assertEqual(self.app.controller.snapshot().target_deg, 90)
        self.assertTrue(self.app.forward_button.instate(["disabled"]))
        self.app.reverse_button.invoke()
        self.assertEqual(self.app.controller.snapshot().target_deg, 90)
        self.app._stop()
        self.assertEqual(self.app.controller.snapshot().motion, "idle")

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


if __name__ == "__main__":
    unittest.main()
