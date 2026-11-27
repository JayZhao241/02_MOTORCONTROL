"""Run the firmware and its desktop console from one host-side entry point."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def python_candidates():
    yield os.environ.get("MOTOR_DESK_PYTHON", "")
    for directory in (ROOT / ".venv", ROOT / "desktop/.runtime/venv"):
        yield str(directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python3"))
    yield sys.executable
    for name in ("python3", "python"):
        yield shutil.which(name) or ""
    for pattern in ("/opt/homebrew/opt/python@*/bin/python3.*", "/usr/local/opt/python@*/bin/python3.*",
                    "/Library/Frameworks/Python.framework/Versions/3.*/bin/python3"):
        from glob import glob
        yield from sorted(glob(pattern), reverse=True)


def ensure_gui_python():
    try:
        import tkinter
        if sys.version_info >= (3, 10) and tkinter.TkVersion >= 8.6:
            return
    except ImportError:
        pass
    seen = set()
    for candidate in python_candidates():
        if not candidate or candidate in seen or not Path(candidate).is_file():
            continue
        seen.add(candidate)
        try:
            result = subprocess.run([candidate, "-c", "import sys, tkinter; assert sys.version_info >= (3, 10); assert tkinter.TkVersion >= 8.6"],
                                    capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            os.execv(candidate, [candidate, str(ROOT / "run.py"), *sys.argv[1:]])
    raise RuntimeError("未找到带 Tkinter 的 Python 3.10+，请先安装 Python/Tk 支持")


def firmware_plan(elf):
    from desktop.stlink_backend import find_tool
    cmake = find_tool("cmake")
    gcc = find_tool("arm-none-eabi-gcc")
    ninja = find_tool("ninja")
    cli = find_tool("STM32_Programmer_CLI")
    env = os.environ.copy()
    directories = [str(Path(tool).parent) for tool in (gcc, ninja, cmake)]
    env["PATH"] = os.pathsep.join([*directories, env.get("PATH", "")])
    commands = [[cmake, "--preset", "Debug"], [cmake, "--build", "--preset", "Debug"]]
    return commands, cli, env


def start_firmware(elf, probe=None, *, dry_run=False):
    from desktop.stlink_backend import run_tool, stlink_probes
    commands, cli, env = firmware_plan(elf)
    if dry_run:
        for command in commands:
            print(subprocess.list2cmdline(command))
        print(subprocess.list2cmdline([cli, "-c", "port=SWD", "mode=UR", "freq=1000", "sn="+(probe or "<ST-Link 序列号>"),
                                      "-w", str(elf), "-v", "-g", "0x08000000"]))
        return probe
    if not probe:
        probes = stlink_probes()
        if len(probes) != 1:
            raise RuntimeError("请连接一只 ST-Link；有多个调试器时使用 --probe 指定序列号")
        probe = probes[0].device
    if not probe.isalnum():
        raise ValueError("ST-Link 序列号无效")
    for command in commands:
        print("[运行项目] " + subprocess.list2cmdline(command), flush=True)
        subprocess.run(command, cwd=ROOT, env=env, check=True)
    if not elf.is_file():
        raise RuntimeError("构建未生成固件 ELF")
    print("[运行项目] 下载、校验并启动固件", flush=True)
    output = run_tool([cli, "-c", "port=SWD", "mode=UR", "freq=1000", "sn="+probe,
                       "-w", str(elf), "-v", "-g", "0x08000000"], timeout=120)
    print(output, flush=True)
    return probe


def main(argv=None):
    parser = argparse.ArgumentParser(description="构建、下载并运行固件，然后启动电机桌面端")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--desktop-only", action="store_true", help="仅启动桌面端，不构建或下载固件")
    group.add_argument("--simulate", action="store_true", help="仅启动桌面端的模拟模式")
    parser.add_argument("--probe", help="指定 ST-Link 序列号")
    parser.add_argument("--dry-run", action="store_true", help="打印执行步骤，不构建、连接或下载")
    args = parser.parse_args(argv)
    from desktop.stlink_backend import DEFAULT_ELF
    elf = DEFAULT_ELF
    probe = args.probe
    if not args.desktop_only and not args.simulate:
        probe = start_firmware(elf, probe, dry_run=args.dry_run)
    if args.dry_run:
        print("[运行项目] 启动桌面端：" + ("模拟连接" if args.simulate else "ST-Link 连接"))
        return 0
    from desktop.app import main as run_desktop
    print("[运行项目] 启动桌面端", flush=True)
    run_desktop(initial_mode="模拟连接" if args.simulate else "ST-Link 连接", elf=elf, probe=probe)
    return 0


if __name__ == "__main__":
    try:
        ensure_gui_python()
        raise SystemExit(main())
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"运行失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
