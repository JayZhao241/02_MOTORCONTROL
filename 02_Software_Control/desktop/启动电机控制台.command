#!/bin/zsh
# Finder does not load the user's interactive shell PATH.
project_dir="${0:A:h:h}"
cd "$project_dir" || exit 1

candidates=(
  "${MOTOR_DESK_PYTHON:-}"
  "$project_dir/.venv/bin/python3"
  "$project_dir/desktop/.runtime/venv/bin/python3"
  "${commands[python3]:-}"
  /opt/homebrew/bin/python3
  /usr/local/bin/python3
  /Library/Frameworks/Python.framework/Versions/3.*/bin/python3(N)
  /opt/homebrew/opt/python@*/bin/python3.*(N)
  /usr/local/opt/python@*/bin/python3.*(N)
)

runtime=""
for candidate in "${candidates[@]}"; do
  [[ -n "$candidate" && -x "$candidate" ]] || continue
  if "$candidate" -c 'import sys; assert sys.version_info >= (3, 10); import tkinter; assert tkinter.TkVersion >= 8.6' >/dev/null 2>&1; then
    runtime="$candidate"
    break
  fi
done

if [[ -z "$runtime" ]]; then
  print '未找到带 Tkinter 的 Python 3.10 或更新版本。'
  print 'Homebrew 用户请安装与 Python 版本对应的 Tk 支持，例如：'
  print '  brew install python-tk@3.14'
  print '安装后重新双击此文件。'
  if [[ -t 0 ]]; then
    read -r '?按回车关闭。'
  fi
  exit 1
fi

print "Python：$runtime"
if [[ "${1:-}" == '--check' ]]; then
  "$runtime" -c 'import sys, tkinter; print("Python", sys.version.split()[0], "· Tk", tkinter.TkVersion)'
  exit $?
fi

mkdir -p "$project_dir/desktop/.runtime" || exit 1
if [[ "${1:-}" == '--run-project' ]]; then
  shift
  launch_args=("$project_dir/run.py" "$@")
else
  launch_args=(-m desktop.app)
fi
"$runtime" "${launch_args[@]}" 2>&1 | /usr/bin/tee "$project_dir/desktop/.runtime/startup.log"
result=${pipestatus[1]}
if [[ "$result" -ne 0 ]]; then
  print "启动失败，日志：$project_dir/desktop/.runtime/startup.log"
  if [[ -t 0 ]]; then
    read -r '?按回车关闭。'
  fi
fi
exit "$result"
