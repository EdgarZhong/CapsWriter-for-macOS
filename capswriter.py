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
import socket
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
VENV_PYTHON  = PROJECT_ROOT / '.venv' / 'bin' / 'python'

# client：直接启动 .app bundle 内的 Mach-O 可执行文件（launcher_embed，内嵌 libpython）
APP_EXECUTABLE = PROJECT_ROOT / 'CapsWriter.app' / 'Contents' / 'MacOS' / 'CapsWriter'

# 两个独立的 launchd 服务标签
LAUNCHD_LABEL_CLIENT = 'com.capswriter.client'
LAUNCHD_LABEL_SERVER = 'com.capswriter.server'

LAUNCHD_PLIST_CLIENT = (
    Path.home() / 'Library' / 'LaunchAgents' / f'{LAUNCHD_LABEL_CLIENT}.plist'
)
LAUNCHD_PLIST_SERVER = (
    Path.home() / 'Library' / 'LaunchAgents' / f'{LAUNCHD_LABEL_SERVER}.plist'
)

LOG_DIR   = Path.home() / '.capswriter' / 'logs'
STATE_DIR = Path.home() / '.capswriter' / 'state'

# client 在启动时写入自己的 PID，用于存活检测
CLIENT_PID_FILE = STATE_DIR / 'client.pid'


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _python() -> str:
    """返回 venv Python 路径；venv 不存在时回退到系统 Python。"""
    return str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


def _run(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


def _launchctl_pid(label: str) -> int | None:
    """通过 launchctl list 获取指定 label 的 PID；服务未运行则返回 None。

    launchctl list 输出格式（tab 分隔）：
        <PID>\t<Exit Status>\t<Label>
    未运行时 PID 列显示 '-'。
    """
    result = subprocess.run(
        ['launchctl', 'list'],
        capture_output=True, text=True
    )
    for line in result.stdout.splitlines():
        parts = line.split('\t')
        if len(parts) == 3 and parts[2].strip() == label:
            try:
                return int(parts[0])   # '-' → ValueError → return None
            except ValueError:
                return None
    return None


def _plist_exists(plist: Path) -> bool:
    return plist.exists()


def _wait_port_ready(host: str = '127.0.0.1', port: int = 6016,
                     timeout: float = 60.0, poll: float = 0.5) -> bool:
    """轮询 TCP 端口，直到连通（server 就绪）或超时。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection((host, port), timeout=1.0)
            s.close()
            return True
        except OSError:
            time.sleep(poll)
    return False


def _client_pid_alive() -> int | None:
    """读取 client PID 文件，验证进程存活；返回 PID 或 None。"""
    try:
        pid = int(CLIENT_PID_FILE.read_text().strip())
        os.kill(pid, 0)   # 仅用于检测，不发信号
        return pid
    except Exception:
        return None


def _client_pids() -> list[int]:
    """返回所有 client 进程 PID（按 .app 可执行文件路径匹配，**不依赖 launchd 标签**）。

    为什么不能只认 launchd 标签：client 是 NSApplication GUI app，一旦注册菜单栏/与
    WindowServer 通信，会被 LaunchServices 从 `com.capswriter.client` 标签「领养」到
    `application.com.capswriter.client.<ASN>` 动态标签。此后 `_launchctl_pid(原标签)`
    与 `launchctl stop 原标签` 都够不到它 → stop 误判「未在运行」→ 孤儿存活、start 再起一个
    → 双实例（D 问题根因）。按可执行文件路径查杀可覆盖任何标签下的全部实例（含多个孤儿）。
    """
    result = subprocess.run(
        ['pgrep', '-f', str(APP_EXECUTABLE)],
        capture_output=True, text=True,
    )
    pids: list[int] = []
    for tok in result.stdout.split():
        try:
            pids.append(int(tok))
        except ValueError:
            pass
    return pids


def _stop_client() -> bool:
    """停止**所有** client 实例（含被 LaunchServices 领养到 application.* 的孤儿）。

    返回是否已全部停止。流程：
    1. `launchctl stop 标签`：若仍被原标签追踪，让 launchd 视为主动停止（KeepAlive 不重启）；
       已被领养时该调用是无害 no-op。
    2. 按身份（exe 路径）SIGTERM 兜底，覆盖任何标签下的实例 → client 借此恢复 Caps remap。
    3. 轮询最多 10s；仍存活则 SIGKILL 强杀。
    """
    # 1. 优雅停原标签（KeepAlive 协调）
    _run(['launchctl', 'stop', LAUNCHD_LABEL_CLIENT], check=False)

    pids = _client_pids()
    if not pids:
        print("客户端未在运行")
        return True

    print(f"正在停止客户端 (pid={', '.join(map(str, pids))}) ...")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    # 等待退出（client 需先恢复 hidutil remap 再退）
    deadline = time.time() + 10.0
    while time.time() < deadline:
        time.sleep(0.5)
        if not _client_pids():
            print("  ✓ 客户端已停止")
            return True

    # 兜底强杀
    stragglers = _client_pids()
    print(f"警告：客户端 {stragglers} 未在 10s 内退出，强制结束 (SIGKILL)")
    for pid in stragglers:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    time.sleep(0.5)
    return not _client_pids()


def _server_port_reachable() -> bool:
    try:
        s = socket.create_connection(('127.0.0.1', 6016), timeout=1.0)
        s.close()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# launchd plist 生成
# ---------------------------------------------------------------------------

def _build_client_plist() -> str:
    """生成 client launchd plist。

    直接运行 .app bundle 的 Mach-O 可执行文件（launcher_embed），
    由 launchd 赋予 CapsWriter.app bundle 身份，TCC 麦克风归属正确。
    SuccessfulExit=false：崩溃（非零退出）时 launchd 自动重启；
    capswriter stop 触发 SIGTERM → exit 0 → 不重启。
    """
    exe = str(APP_EXECUTABLE)
    cwd = str(PROJECT_ROOT)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stdout_log = str(LOG_DIR / 'client.stdout.log')
    stderr_log = str(LOG_DIR / 'client.stderr.log')

    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL_CLIENT}</string>

    <key>Program</key>
    <string>{exe}</string>

    <key>WorkingDirectory</key>
    <string>{cwd}</string>

    <!-- 登录后自动启动 -->
    <key>RunAtLoad</key>
    <true/>

    <!-- 崩溃（非零退出）时 launchd 重启；正常停止（exit 0）不重启 -->
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <key>StandardOutPath</key>
    <string>{stdout_log}</string>
    <key>StandardErrorPath</key>
    <string>{stderr_log}</string>
</dict>
</plist>
"""


def _build_server_plist() -> str:
    """生成 server launchd plist。

    通过 venv Python 运行 start_server.py（qwen_asr_mlx ASR 推理服务）。
    start_server.py 注册了 SIGTERM → os._exit(0)，
    因此 capswriter stop 后 launchd 不会重启 server。
    server 在 client 断连 60s 后会主动 exit 0（M2 实现）。
    """
    python = _python()
    server_script = str(PROJECT_ROOT / 'start_server.py')
    cwd = str(PROJECT_ROOT)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stdout_log = str(LOG_DIR / 'server.stdout.log')
    stderr_log = str(LOG_DIR / 'server.stderr.log')

    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL_SERVER}</string>

    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{server_script}</string>
    </array>

    <key>WorkingDirectory</key>
    <string>{cwd}</string>

    <!-- 登录后自动启动 -->
    <key>RunAtLoad</key>
    <true/>

    <!-- 崩溃（非零退出）时 launchd 重启；正常停止（exit 0）不重启 -->
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

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
    """注册 server + client 两个 launchd 服务（各自独立，互不依赖）。"""
    LAUNCHD_PLIST_CLIENT.parent.mkdir(parents=True, exist_ok=True)

    # server plist
    if not _plist_exists(LAUNCHD_PLIST_SERVER):
        LAUNCHD_PLIST_SERVER.write_text(_build_server_plist())
        print(f"已写入 server plist: {LAUNCHD_PLIST_SERVER}")
        _run(['launchctl', 'load', str(LAUNCHD_PLIST_SERVER)])
        print("识别引擎 launchd 服务已注册")
    else:
        print("识别引擎 launchd 服务已安装，跳过（如需更新请先 uninstall）")

    # client plist
    if not _plist_exists(LAUNCHD_PLIST_CLIENT):
        LAUNCHD_PLIST_CLIENT.write_text(_build_client_plist())
        print(f"已写入 client plist: {LAUNCHD_PLIST_CLIENT}")
        _run(['launchctl', 'load', str(LAUNCHD_PLIST_CLIENT)])
        print("客户端 launchd 服务已注册")
    else:
        print("客户端 launchd 服务已安装，跳过（如需更新请先 uninstall）")

    print("\nCapsWriter 将在登录后自动启动。运行 capswriter status 查看当前状态。")
    return 0


def cmd_uninstall(args) -> int:
    """注销 launchd 服务（先停 client 再停 server，确保 remap 恢复）。"""
    # client 先停（持有 remap 生命周期，必须在 server 前清理）
    # 用 _stop_client() 按身份查杀，覆盖被领养到 application.* 的孤儿，避免 unload 后残留
    _stop_client()

    if _plist_exists(LAUNCHD_PLIST_CLIENT):
        _run(['launchctl', 'unload', str(LAUNCHD_PLIST_CLIENT)], check=False)
        LAUNCHD_PLIST_CLIENT.unlink()
        print(f"已注销客户端服务，删除: {LAUNCHD_PLIST_CLIENT}")
    else:
        print("客户端 launchd 服务未注册，跳过")

    # server 后停
    if _launchctl_pid(LAUNCHD_LABEL_SERVER) is not None:
        _run(['launchctl', 'stop', LAUNCHD_LABEL_SERVER], check=False)
        print("已发送停止信号到识别引擎")

    if _plist_exists(LAUNCHD_PLIST_SERVER):
        _run(['launchctl', 'unload', str(LAUNCHD_PLIST_SERVER)], check=False)
        LAUNCHD_PLIST_SERVER.unlink()
        print(f"已注销识别引擎服务，删除: {LAUNCHD_PLIST_SERVER}")
    else:
        print("识别引擎 launchd 服务未注册，跳过")

    return 0


def _read_status_json() -> dict | None:
    """读取 status.json，返回解析后的字典；文件不存在或解析失败返回 None。"""
    status_file = STATE_DIR / 'status.json'
    try:
        return __import__('json').loads(status_file.read_text())
    except Exception:
        return None


def _status_is_fresh(status: dict, max_age_secs: float = 10.0) -> bool:
    """检查 status.json 的 last_heartbeat 是否在 max_age_secs 内（判断 client 是否存活）。"""
    from datetime import datetime
    try:
        ts = datetime.fromisoformat(status.get('last_heartbeat', ''))
        age = (datetime.now() - ts).total_seconds()
        return age <= max_age_secs
    except Exception:
        return False


def cmd_start(args) -> int:
    """启动 server 和 client，阻塞等待直到 client 变为 ready 或明确失败。"""
    # 必须先安装才能通过 launchctl 管理
    if not _plist_exists(LAUNCHD_PLIST_SERVER) or not _plist_exists(LAUNCHD_PLIST_CLIENT):
        print("launchd 服务未注册，请先执行 capswriter install")
        return 1

    print("正在启动 CapsWriter ...")

    # 1. 启动 server
    server_pid = _launchctl_pid(LAUNCHD_LABEL_SERVER)
    if server_pid is None:
        _run(['launchctl', 'start', LAUNCHD_LABEL_SERVER], check=False)

    # 等待 server 端口就绪（server 加载模型需要时间）
    print("  等待识别引擎就绪（最长 60s）...", end='', flush=True)
    if not _wait_port_ready(timeout=60.0):
        print()
        print("  ✗ 识别引擎 60s 内未就绪，请检查日志：")
        print(f"    cat {LOG_DIR / 'server.stderr.log'}")
        return 1
    print()
    print("  ✓ 识别引擎已就绪")

    # 2. 启动 client（先按身份查重，已有实例/孤儿则不再起，避免双实例）
    if not _client_pids():
        _run(['launchctl', 'start', LAUNCHD_LABEL_CLIENT], check=False)
    else:
        print("  客户端已在运行，跳过启动")

    # 等待 client status.json 出现且状态为 ready（最多 30s）
    print("  等待客户端就绪...", end='', flush=True)
    deadline = time.time() + 30.0
    last_state = None
    while time.time() < deadline:
        time.sleep(0.5)
        s = _read_status_json()
        if s is None:
            continue
        state = s.get('state', '')
        if state != last_state:
            print(f"\r  等待客户端就绪... [{state}]", end='', flush=True)
            last_state = state
        if state == 'ready':
            break
    else:
        print()
        s = _read_status_json()
        if s is None:
            print("  ✗ 客户端 30s 内未启动，请检查日志：")
            print(f"    cat {LOG_DIR / 'client.stderr.log'}")
        elif s.get('accessibility_ok') is False and s.get('state') != 'ready':
            # 特定场景：辅助功能权限未授权导致 CGEventTap 无法建立
            print("  ✗ 辅助功能权限未授权，系统设置已自动打开")
            print("    请在「辅助功能」列表中找到 CapsWriter 并开启授权，")
            print("    授权后 CapsWriter 将自动恢复，无需重启。")
        else:
            print(f"  ✗ 客户端超时（当前状态：{s.get('state', '?')}），请检查日志：")
            print(f"    cat {LOG_DIR / 'client.stderr.log'}")
        return 1

    print()
    print("  ✓ 客户端已就绪")
    print("\nCapsWriter 运行中")
    return 0


def cmd_stop(args) -> int:
    """停止 client 和 server（client 优先，等待 remap 恢复后再停 server）。"""
    # 1. 先停 client（按身份查杀，覆盖被领养到 application.* 的孤儿；client 借此恢复 remap）
    _stop_client()

    # 2. 再停 server
    server_pid = _launchctl_pid(LAUNCHD_LABEL_SERVER)
    if server_pid is not None:
        print(f"正在停止识别引擎 (pid={server_pid}) ...")
        _run(['launchctl', 'stop', LAUNCHD_LABEL_SERVER], check=False)
        print("  ✓ 识别引擎停止信号已发送")
    else:
        print("识别引擎未在运行")

    print("已停止")
    return 0


def cmd_restart(args) -> int:
    """重启 client 和 server。"""
    rc = cmd_stop(args)
    if rc != 0:
        return rc
    time.sleep(1)
    return cmd_start(args)


def cmd_status(args) -> int:
    """显示 CapsWriter 运行状态快照（优先读 status.json，降级到 launchctl 检测）。"""
    from datetime import datetime

    client_pids = _client_pids()                       # 按身份查，覆盖被领养的孤儿
    client_pid  = client_pids[0] if client_pids else None
    server_pid  = _launchctl_pid(LAUNCHD_LABEL_SERVER)
    server_port = _server_port_reachable()
    installed   = _plist_exists(LAUNCHD_PLIST_CLIENT) and _plist_exists(LAUNCHD_PLIST_SERVER)
    status      = _read_status_json()

    # 判断 client 是否真的存活（按进程身份，而非 launchd 标签，避免漏掉孤儿）
    client_alive = bool(client_pids)
    status_stale = False
    if status is not None and not _status_is_fresh(status):
        status_stale = True  # status.json 存在但超过 10s 无心跳，可能僵死

    print("CapsWriter for macOS 状态")
    print("-" * 40)

    # 多实例告警：正常应只有 1 个 client，>1 多半是孤儿残留（见 D 问题）
    if len(client_pids) > 1:
        print(f"  ⚠ 检测到 {len(client_pids)} 个客户端实例 "
              f"(pid={', '.join(map(str, client_pids))})，疑似孤儿残留，建议 capswriter restart")

    if status is not None and client_alive:
        # 有 status.json + launchd 确认运行 → 完整显示
        state_map = {
            'starting':   '启动中',
            'connecting': '等待识别引擎',
            'ready':      '就绪 ✓',
            'recording':  '录音中 🔴',
            'error':      '错误 ✗',
        }
        state_label = state_map.get(status.get('state', ''), status.get('state', '?'))
        if status_stale:
            state_label += '  ⚠ 心跳超时，可能已僵死'

        print(f"  CapsWriter for macOS  [{state_label}]")
        print(f"  识别引擎    : {'已连接 ✓' if status.get('server_connected') else '未连接'}"
              + (f' (port 6016 {"就绪" if server_port else "不可达"})' if server_pid else ''))
        print(f"  辅助功能    : {'已授权 ✓' if status.get('accessibility_ok') else '未授权'}")
        print(f"  麦克风      : {'已授权 ✓' if status.get('microphone_ok') else '未授权'}")

        # 运行时长
        try:
            started = datetime.fromisoformat(status.get('started_at', ''))
            elapsed = int((datetime.now() - started).total_seconds())
            mins, secs = divmod(elapsed, 60)
            hours, mins = divmod(mins, 60)
            uptime = f"{hours}h {mins}m" if hours else f"{mins}m {secs}s"
            print(f"  运行时长    : {uptime}")
        except Exception:
            pass

        if status.get('last_error'):
            print(f"  最近错误    : {status['last_error']}")
    else:
        # 降级显示
        def _fmt(pid, extra=''):
            return f'运行中 (pid={pid}){extra}' if pid else '未运行'

        print(f"  客户端      : {_fmt(client_pid)}")
        print(f"  识别引擎    : {_fmt(server_pid, ' [端口就绪]' if server_port else '')}")
        if not client_alive and not server_pid:
            print("\nCapsWriter 未运行，执行 capswriter start 启动")

    print(f"  launchd 注册: {'已注册' if installed else '未注册，执行 capswriter install 启用开机自启'}")

    if client_pid:
        print()
        _show_remap_brief()

    return 0 if (client_pid and server_pid) else 1


def _show_remap_brief() -> None:
    """简要展示 Caps Lock remap 状态（在 status 里复用）。"""
    result = subprocess.run(
        [_python(), '-m', 'core.client.shortcut.macos_caps_remap', 'status'],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True
    )
    if result.stdout.strip():
        print("Remap 状态:")
        for line in result.stdout.strip().splitlines():
            print(f"  {line}")


def cmd_doctor(args) -> int:
    """检查运行环境与权限（主动探测，不依赖运行状态）。"""
    issues = []
    ok = []

    # 1. venv Python
    if VENV_PYTHON.exists():
        ok.append(f"venv Python: {VENV_PYTHON}")
    else:
        issues.append("未找到 .venv/bin/python，请先创建 venv 并安装依赖")

    # 2. CapsWriter.app 可执行文件
    if APP_EXECUTABLE.exists():
        ok.append(f"CapsWriter.app: {APP_EXECUTABLE}")
    else:
        issues.append(f"未找到 app 可执行文件: {APP_EXECUTABLE}\n"
                      "  → 请执行 bash build_launcher.sh 编译 C 启动器")

    # 3. Accessibility（辅助功能）权限 —— 通过尝试创建 CGEventTap 检测
    import Quartz as _Quartz
    _acc_ok = False
    try:
        _mask = _Quartz.CGEventMaskBit(_Quartz.kCGEventKeyDown)
        _test_tap = _Quartz.CGEventTapCreate(
            _Quartz.kCGHIDEventTap, _Quartz.kCGHeadInsertEventTap,
            _Quartz.kCGEventTapOptionDefault, _mask, lambda *a: a[2], None,
        )
        if _test_tap is not None:
            _Quartz.CGEventTapEnable(_test_tap, False)
            _acc_ok = True
    except Exception:
        pass

    if _acc_ok:
        ok.append("辅助功能（Accessibility）权限：已授权，CGEventTap 可用")
    else:
        issues.append(
            "辅助功能（Accessibility）权限：未授权（CGEventTap 创建失败）\n"
            "  → 即将自动打开：系统设置 → 隐私与安全性 → 辅助功能\n"
            "  → 找到 CapsWriter，关闭后重新打开开关（或删除重新添加）"
        )
        subprocess.Popen([
            'open',
            'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility',
        ])

    # 4. server 端口可达性（是否正在运行）
    if _server_port_reachable():
        ok.append("识别引擎端口 6016：可达（server 正在运行）")
    else:
        issues.append("识别引擎端口 6016：不可达（server 未运行）\n"
                      "  → 执行 capswriter start 启动")

    # 5. launchd 注册状态
    installed = _plist_exists(LAUNCHD_PLIST_CLIENT) and _plist_exists(LAUNCHD_PLIST_SERVER)
    if installed:
        ok.append("launchd 服务：已注册（开机自启已配置）")
    else:
        issues.append("launchd 服务：未注册\n  → 执行 capswriter install 配置开机自启")

    print("Doctor 检查结果")
    print("=" * 50)
    for item in ok:
        print(f"  ✓  {item}")
    for item in issues:
        print(f"  ✗  {item}")

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
      注册 launchd 服务（client + server 各自独立），使 CapsWriter
      在登录后自动启动。首次使用必须执行。

  capswriter uninstall
      注销 launchd 服务，取消开机自启。不删除项目文件。

  capswriter start
      启动 CapsWriter（先启动识别引擎，等待就绪后再启动客户端）。

  capswriter stop
      停止 CapsWriter（先停客户端并恢复 Caps Lock 映射，再停识别引擎）。

  capswriter restart
      重启 CapsWriter（stop + start）。修改配置后使用。

【状态查看】

  capswriter status
      显示客户端、识别引擎的运行状态与 launchd 注册情况。

  capswriter doctor
      主动检查环境与权限（不依赖运行状态）：
        · venv Python 是否存在
        · CapsWriter.app 可执行文件是否已编译
        · Accessibility（辅助功能）权限是否已授权
        · 识别引擎端口 6016 是否可达
        · launchd 服务是否已注册

【Caps Lock 映射管理】

  capswriter remap status
      查看当前系统 UserKeyMapping 与 CapsWriter 保存的映射快照。

  capswriter remap restore
      将 Caps Lock 映射恢复为 client 启动前的原始状态。
      ⚠  仅限 client 未运行时使用，运行中请先执行 stop。

  capswriter remap clear --force
      清空系统全部 UserKeyMapping（包括用户自定义映射）。
      ⚠  危险救援命令，仅在 restore 无效时使用。

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
       capswriter remap restore   # 恢复快照
       # 或：capswriter remap clear --force（极端情况）
""".strip())
    return 0


# ---------------------------------------------------------------------------
# remap 子命令（代理到 macos_caps_remap CLI）
# ---------------------------------------------------------------------------

def cmd_remap(args) -> int:
    """代理到 macos_caps_remap 的 CLI（status / restore / clear --force）。"""
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

    class _Parser(argparse.ArgumentParser):
        def error(self, message):
            sys.stderr.write(f"错误：{message}\n运行 'capswriter help' 查看所有可用命令。\n")
            sys.exit(2)

    parser = _Parser(
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
