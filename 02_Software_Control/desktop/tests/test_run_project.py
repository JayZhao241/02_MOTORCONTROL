from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run as project
from desktop.stlink_backend import ProbeInfo


class ProjectRunTests(unittest.TestCase):
    def test_build_then_download_verify_start(self):
        with tempfile.TemporaryDirectory() as directory:
            elf = Path(directory) / "firmware with spaces.elf"
            elf.touch()
            with patch.object(project, "firmware_plan", return_value=([["cmake", "configure"], ["cmake", "build"]], "cli", {"PATH": "tools"})), \
                 patch("desktop.stlink_backend.stlink_probes", return_value=[ProbeInfo("ABC123")]), \
                 patch.object(project.subprocess, "run") as build, \
                 patch("desktop.stlink_backend.run_tool", return_value="started") as download:
                events = []
                build.side_effect = lambda *a, **kw: events.append("build")
                download.side_effect = lambda *a, **kw: events.append("download") or "started"
                self.assertEqual(project.start_firmware(elf), "ABC123")
                self.assertEqual(events, ["build", "build", "download"])
                self.assertEqual(download.call_args.args[0], ["cli", "-c", "port=SWD", "mode=UR", "freq=1000", "sn=ABC123",
                                                              "-w", str(elf), "-v", "-g", "0x08000000"])

    def test_dry_run_never_builds_or_connects(self):
        with patch.object(project, "firmware_plan", return_value=([["cmake", "build"]], "cli", {})), \
             patch("desktop.stlink_backend.stlink_probes") as probes, \
             patch.object(project.subprocess, "run") as build, \
             patch("desktop.stlink_backend.run_tool") as download:
            project.start_firmware(Path("firmware.elf"), dry_run=True)
            probes.assert_not_called()
            build.assert_not_called()
            download.assert_not_called()

    def test_multiple_probes_require_explicit_selection(self):
        with patch.object(project, "firmware_plan", return_value=([], "cli", {})), \
             patch("desktop.stlink_backend.stlink_probes", return_value=[ProbeInfo("AAA"), ProbeInfo("BBB")]), \
             patch("desktop.stlink_backend.run_tool") as download:
            with self.assertRaisesRegex(RuntimeError, "--probe"):
                project.start_firmware(Path("firmware.elf"))
            download.assert_not_called()

    def test_simulation_skips_firmware(self):
        with patch.object(project, "start_firmware") as firmware, patch("desktop.app.main") as gui:
            self.assertEqual(project.main(["--simulate"]), 0)
            firmware.assert_not_called()
            self.assertEqual(gui.call_args.kwargs["initial_mode"], "模拟连接")

    def test_desktop_only_skips_firmware(self):
        with patch.object(project, "start_firmware") as firmware, patch("desktop.app.main") as gui:
            project.main(["--desktop-only"])
            firmware.assert_not_called()
            self.assertEqual(gui.call_args.kwargs["initial_mode"], "ST-Link 连接")

    def test_default_run_opens_gui_after_firmware(self):
        events = []
        with patch.object(project, "start_firmware", side_effect=lambda *a, **kw: events.append("firmware") or "ABC"), \
             patch("desktop.app.main", side_effect=lambda **kw: events.append("gui")) as gui:
            project.main([])
            self.assertEqual(events, ["firmware", "gui"])
            self.assertEqual(gui.call_args.kwargs["probe"], "ABC")

    def test_build_failure_does_not_open_gui(self):
        with patch.object(project, "start_firmware", side_effect=RuntimeError("构建失败")), patch("desktop.app.main") as gui:
            with self.assertRaises(RuntimeError):
                project.main([])
            gui.assert_not_called()


if __name__ == "__main__":
    unittest.main()
