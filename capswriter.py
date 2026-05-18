#!/usr/bin/env python3
# coding: utf-8
"""
capswriter — CapsWriter for macOS 控制命令行工具。

用法：
  capswriter install     # 注册 launchd 服务，设置开机自启
  capswriter uninstall   # 注销 launchd 服务
  capswriter start       # 启动后台服务
  capswriter stop        # 停止后台服务
  capswriter restart     # 重启后台服务
  capswriter status      # 查看运行状态
  capswriter doctor      # 环境与权限检查

  capswriter remap status           # 查看 Caps Lock remap 状态
  capswriter remap restore          # 恢复 Caps Lock 原始映射（仅限 client 未运行时）
  capswriter remap clear --force    # 清空所有 UserKeyMapping（救援命令）
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
VENV_PYTHON = PROJECT_ROOT / '.venv' / 'bin' / 'python'
CAPSWRITERD = PROJECT_ROOT / 'capswriterd.py'

LAUNCHD_LABEL = 'com.capswriter.agent'
LAUNCHD_PLIST = Path.home() / 'Library' / 'LaunchAgents' / f'{LAUNCHD_LABEL}.plist'

LOG_DIR = Path.home() / '.capswriter' / 'logs'


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _python() -> str:
    return str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


def _run(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


def _capswriterd_status() -> tuple[bool, int | None]:
    """返回 (is_running, pid)。"""
    result = subprocess.run(
        [_python(), str(CAPSWRITERD), 'status'],
        capture_output=True, text=True
    )
    line = result.stdout.strip()
    if line.startswith('running'):
        try:
            pid = int(line.split('pid=')[1])
            return True, pid
        except (IndexError, ValueError):
            return True, None
    return False, None


def _launchd_installed() -> bool:
    return LAUNCHD_PLIST.exists()


def _launchd_loaded() -> bool:
    result = subprocess.run(
        ['launchctl', 'list', LAUNCHD_LABEL],
        capture_output=True, text=True
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# launchd plist 生成
# ---------------------------------------------------------------------------

def _build_plist() -> str:
    python = _python()
    capswriterd = str(CAPSWRITERD)
    cwd = str(PROJECT_ROOT)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stdout_log = str(LOG_DIR / 'capswriterd.stdout.log')
    stderr_log = str(LOG_DIR / 'capswriterd.stderr.log')

    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL}</string>

    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{capswriterd}</string>
        <string>run</string>
    </array>

    <key>WorkingDirectory</key>
    <string>{cwd}</string>

    <!-- 登录后自动启动 -->
    <key>RunAtLoad</key>
    <true/>

    <!-- 意外退出后由 launchd 自动拉起 -->
    <key>KeepAlive</key>
    <true/>

    <!-- 标准输出重定向（Python 内部日志走 logs/ 目录，此处只捕获意外输出） -->
    <key>StandardOutPath</key>
    <string>{stdout_log}</string>
    <key>StandardErrorPath</key>
    <string>{stderr_log}</string>
</dict>
</plist>
"""


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------

def cmd_install(args) -> int:
    """注册 launchd 服务并立即启动。"""
    if _launchd_installed():
        print(f"已安装（{LAUNCHD_PLIST}）")
        if not _launchd_loaded():
            print("重新加载 ...")
            _run(['launchctl', 'load', str(LAUNCHD_PLIST)])
        return 0

    plist_content = _build_plist()
    LAUNCHD_PLIST.parent.mkdir(parents=True, exist_ok=True)
    LAUNCHD_PLIST.write_text(plist_content)
    print(f"已写入 plist: {LAUNCHD_PLIST}")

    _run(['launchctl', 'load', str(LAUNCHD_PLIST)])
    print("launchd 服务已注册，CapsWriter 将在登录后自动启动。")
    print("（首次启动可能需要几秒，请稍候）")
    return 0


def cmd_uninstall(args) -> int:
    """注销 launchd 服务（不删除项目文件）。"""
    if _launchd_loaded():
        _run(['launchctl', 'unload', str(LAUNCHD_PLIST)], check=False)
        print("launchd 服务已注销")
    else:
        print("launchd 服务未注册，跳过")

    if LAUNCHD_PLIST.exists():
        LAUNCHD_PLIST.unlink()
        print(f"已删除 plist: {LAUNCHD_PLIST}")

    return 0


def cmd_start(args) -> int:
    """启动 capswriterd（若已在运行则跳过）。"""
    running, pid = _capswriterd_status()
    if running:
        print(f"已在运行 (pid={pid})")
        return 0

    if _launchd_loaded():
        # 已注册 launchd，通过 launchctl 拉起
        _run(['launchctl', 'start', LAUNCHD_LABEL], check=False)
        print("通过 launchd 启动 ...")
    else:
        # 直接后台启动 capswriterd
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stdout_path = LOG_DIR / 'capswriterd.stdout.log'
        stderr_path = LOG_DIR / 'capswriterd.stderr.log'
        with open(stdout_path, 'a') as out, open(stderr_path, 'a') as err:
            subprocess.Popen(
                [_python(), str(CAPSWRITERD), 'run'],
                cwd=str(PROJECT_ROOT),
                stdout=out,
                stderr=err,
                start_new_session=True,
            )
        print("capswriterd 已在后台启动")

    # 等待就绪确认
    for _ in range(10):
        time.sleep(0.5)
        running, pid = _capswriterd_status()
        if running:
            print(f"运行中 (pid={pid})")
            return 0

    print("警告：启动后未能确认 capswriterd 运行状态，请通过 capswriter status 确认")
    return 1


def cmd_stop(args) -> int:
    """停止 capswriterd。"""
    running, pid = _capswriterd_status()

    if _launchd_loaded():
        _run(['launchctl', 'stop', LAUNCHD_LABEL], check=False)

    if not running or pid is None:
        print("未在运行")
        return 0

    try:
        os.kill(pid, signal.SIGTERM)
        print(f"已发送 SIGTERM 到 pid={pid}")
    except ProcessLookupError:
        print("进程已不存在")
        return 0
    except Exception as e:
        print(f"发送信号失败: {e}")
        return 1

    # 等待退出
    for _ in range(20):
        time.sleep(0.5)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            print("已停止")
            return 0

    print(f"警告：pid={pid} 在 10 s 内未退出，可能需要手动处理")
    return 1


def cmd_restart(args) -> int:
    """重启 capswriterd。"""
    rc = cmd_stop(args)
    if rc != 0:
        return rc
    time.sleep(1)
    return cmd_start(args)


def cmd_status(args) -> int:
    """显示运行状态。"""
    running, pid = _capswriterd_status()
    installed = _launchd_installed()
    loaded = _launchd_loaded()

    print("CapsWriter for macOS 状态")
    print("-" * 40)
    print(f"  capswriterd  : {'运行中 (pid=' + str(pid) + ')' if running else '未运行'}")
    print(f"  launchd 注册 : {'已注册' if installed else '未注册'}")
    print(f"  launchd 加载 : {'已加载' if loaded else '未加载'}")
    print(f"  plist 路径   : {LAUNCHD_PLIST}")

    # 如果正在运行，顺带展示 remap 状态
    if running:
        print()
        _show_remap_brief()

    return 0 if running else 1


def _show_remap_brief() -> None:
    """简要展示 remap 状态（在 status 里复用）。"""
    result = subprocess.run(
        [_python(), '-m', 'core.client.shortcut.macos_caps_remap', 'status'],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True
    )
    if result.stdout.strip():
        print("Remap 状态:")
        for line in result.stdout.strip().splitlines():
            print(f"  {line}")


def cmd_doctor(args) -> int:
    """检查运行环境与权限。"""
    issues = []
    ok = []

    # 1. Python / venv
    if VENV_PYTHON.exists():
        ok.append(f"venv Python: {VENV_PYTHON}")
    else:
        issues.append(f"未找到 .venv/bin/python，请先运行 uv sync 或 pip install -r requirements.txt")

    # 2. 检查 capswriterd.py 存在
    if CAPSWRITERD.exists():
        ok.append(f"capswriterd.py: {CAPSWRITERD}")
    else:
        issues.append(f"未找到 capswriterd.py")

    # 3. 检查辅助功能权限（Accessibility）
    result = subprocess.run(
        ['osascript', '-e',
         'tell application "System Events" to get name of processes'],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        ok.append("辅助功能（Accessibility）权限：已授权（自动粘贴可用）")
    else:
        issues.append(
            "辅助功能（Accessibility）权限：未授权\n"
            "  → 前往：系统设置 → 隐私与安全性 → 辅助功能\n"
            "  → 添加运行 start_client.py 的终端 app\n"
            "  → 无此权限时识别结果仍会写入剪贴板，但无法自动粘贴上屏"
        )

    # 4. 检查 Input Monitoring 权限（通过 pynput 间接检测）
    #    无法直接 API 查询，只能提示用户确认
    ok.append("输入监控（Input Monitoring）权限：请手动确认（pynput F18 监听需要）")

    # 5. 检查 server 端口可达性
    import socket
    try:
        s = socket.create_connection(('127.0.0.1', 6016), timeout=1.0)
        s.close()
        ok.append("server WebSocket 端口 6016：可达（server 正在运行）")
    except OSError:
        issues.append("server WebSocket 端口 6016：不可达（server 未运行或端口被占用）")

    print("Doctor 检查结果")
    print("=" * 50)
    for item in ok:
        print(f"  [OK] {item}")
    for item in issues:
        print(f"  [!!] {item}")

    return 1 if issues else 0


# ---------------------------------------------------------------------------
# help 子命令
# ---------------------------------------------------------------------------

def cmd_help(args) -> int:
    """打印详细命令帮助。"""
    print("""
CapsWriter for macOS — 使用帮助
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【服务管理】

  capswriter install
      注册 launchd 服务，使 CapsWriter 在登录后自动启动。
      首次使用必须执行。

  capswriter uninstall
      注销 launchd 服务，取消开机自启。不删除项目文件。

  capswriter start
      在后台启动 CapsWriter（包含 server 和 client）。

  capswriter stop
      停止后台服务，并恢复 Caps Lock 原始键盘映射。

  capswriter restart
      重启后台服务（stop + start）。修改配置后使用。

【状态查看】

  capswriter status
      显示 capswriterd 运行状态、launchd 注册情况
      以及当前 Caps Lock 键盘映射快照。

  capswriter doctor
      检查运行环境与权限：
        · .venv Python 是否存在
        · Accessibility（辅助功能）权限是否已授权（自动粘贴需要）
        · Input Monitoring（输入监控）权限提示（F18 监听需要）
        · server WebSocket 端口 6016 是否可达

【Caps Lock 映射管理】

  capswriter remap status
      查看当前系统 UserKeyMapping，以及 CapsWriter 保存的
      键盘映射快照（含创建时间、active 状态、client PID）。

  capswriter remap restore
      将 Caps Lock 映射恢复为 client 启动前的原始状态。
      ⚠  仅限 client 未运行时使用，运行中请先执行 stop。

  capswriter remap clear --force
      清空系统全部 UserKeyMapping（包括用户自定义的其他映射）。
      ⚠  危险救援命令，仅在 restore 无效时使用。
      ⚠  仅限 client 未运行时使用。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
【典型使用流程】

  1. 首次安装：
       capswriter install        # 注册 launchd，设置开机自启
       capswriter status         # 确认运行状态

  2. 日常使用：
       按住 Caps Lock → 说话 → 松开即上屏

  3. 修改配置后生效：
       # 修改 config_client.py / config_server.py
       capswriter restart

  4. Caps Lock 卡在 F18 映射（救援）：
       capswriter stop
       capswriter remap restore  # 恢复快照
       # 或：capswriter remap clear --force （极端情况）
""".strip())
    return 0


# ---------------------------------------------------------------------------
# remap 子命令（代理到 macos_caps_remap CLI）
# ---------------------------------------------------------------------------

def cmd_remap(args) -> int:
    """代理到 macos_caps_remap 的 CLI。"""
    # 把 remap 后的参数透传过去
    extra = args.remap_args
    result = subprocess.run(
        [_python(), '-m', 'core.client.shortcut.macos_caps_remap'] + extra,
        cwd=str(PROJECT_ROOT),
    )
    return result.returncode


# ---------------------------------------------------------------------------
# 参数解析与入口
# ---------------------------------------------------------------------------

def _build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        prog='capswriter',
        description='CapsWriter for macOS 控制工具',
        add_help=True,
    )
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('install',   help='注册 launchd 服务（开机自启）')
    sub.add_parser('uninstall', help='注销 launchd 服务')
    sub.add_parser('start',     help='启动后台服务')
    sub.add_parser('stop',      help='停止后台服务')
    sub.add_parser('restart',   help='重启后台服务')
    sub.add_parser('status',    help='查看运行状态')
    sub.add_parser('doctor',    help='环境与权限检查')
    sub.add_parser('help',      help='显示详细帮助')

    remap_p = sub.add_parser('remap', help='Caps Lock remap 管理')
    remap_p.add_argument('remap_args', nargs=argparse.REMAINDER,
                         help='remap 子命令（status / restore / clear --force）')

    return parser


COMMANDS = {
    'install':   cmd_install,
    'uninstall': cmd_uninstall,
    'start':     cmd_start,
    'stop':      cmd_stop,
    'restart':   cmd_restart,
    'status':    cmd_status,
    'doctor':    cmd_doctor,
    'help':      cmd_help,
    'remap':     cmd_remap,
}


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    fn = COMMANDS.get(args.command)
    if fn is None:
        parser.print_help()
        return 1
    return fn(args)


if __name__ == '__main__':
    raise SystemExit(main())
