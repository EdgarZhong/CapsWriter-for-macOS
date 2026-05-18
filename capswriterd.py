#!/usr/bin/env python3
# coding: utf-8
"""
CapsWriter 后台守护进程 (capswriterd)。

职责：
  1. 单例运行 —— 通过 PID 锁文件确保只有一个实例。
  2. 启动并监控 server 和 client 子进程。
  3. 停止顺序：先 client 后 server（client 持有 remap 生命周期）。
  4. 接入既有日志系统（logs/capswriterd_latest.log）。
  5. 支持被 launchd 直接拉起。

用法：
  capswriterd.py run      # 启动守护进程（前台运行，由 launchd / capswriter 管理）
  capswriterd.py status   # 查看运行状态（供 capswriter status 内部调用）
"""
from __future__ import annotations

import atexit
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 项目根目录（本文件位于项目根）
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# 状态目录与文件
# ---------------------------------------------------------------------------
STATE_DIR = Path.home() / '.capswriter' / 'state'
PID_FILE = STATE_DIR / 'capswriterd.pid'

# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

def _setup_log():
    """初始化 capswriterd 专属日志。"""
    sys.path.insert(0, str(PROJECT_ROOT))
    from core.logger import setup_logger
    return setup_logger('capswriterd', level='INFO')


logger = None  # 延迟初始化，防止 import 阶段引入问题


# ---------------------------------------------------------------------------
# PID 锁文件
# ---------------------------------------------------------------------------

def _write_pid(pid: int) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid))


def _clear_pid() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def read_pid() -> Optional[int]:
    """读取 PID 文件，返回 PID；文件不存在或内容无效时返回 None。"""
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return None


def is_running(pid: Optional[int] = None) -> bool:
    """检查给定 PID（或 PID 文件里的 PID）是否对应存活进程。"""
    pid = pid or read_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 进程存在但无权发信号


# ---------------------------------------------------------------------------
# 服务端就绪检测
# ---------------------------------------------------------------------------

def _wait_server_ready(host: str = '127.0.0.1', port: int = 6016,
                       timeout: float = 60.0, poll: float = 0.5) -> bool:
    """轮询 TCP 端口，直到连通或超时。返回是否就绪。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection((host, port), timeout=1.0)
            s.close()
            return True
        except OSError:
            time.sleep(poll)
    return False


# ---------------------------------------------------------------------------
# 守护进程主逻辑
# ---------------------------------------------------------------------------

class CapsWriterDaemon:
    """管理 server / client 子进程的单例守护进程。"""

    def __init__(self) -> None:
        self.server: Optional[subprocess.Popen] = None
        self.client: Optional[subprocess.Popen] = None
        self._stopping = False

    # ------------------------------------------------------------------
    # 子进程构建
    # ------------------------------------------------------------------

    def _python(self) -> str:
        """优先使用 .venv 里的 Python，与项目依赖保持一致。"""
        venv_python = PROJECT_ROOT / '.venv' / 'bin' / 'python'
        if venv_python.exists():
            return str(venv_python)
        return sys.executable

    def _child_env(self) -> dict:
        """子进程环境变量：注入标记，防止 start_client.py 再次进入 supervisor。"""
        env = os.environ.copy()
        env['CAPSWRITER_MACOS_SUPERVISOR_CHILD'] = '1'
        return env

    def _start_server(self) -> subprocess.Popen:
        cmd = [self._python(), str(PROJECT_ROOT / 'start_server.py')]
        logger.info("[capswriterd] 启动 server: %s", cmd)
        return subprocess.Popen(cmd, cwd=str(PROJECT_ROOT))

    def _start_client(self) -> subprocess.Popen:
        cmd = [self._python(), str(PROJECT_ROOT / 'start_client.py')]
        logger.info("[capswriterd] 启动 client: %s", cmd)
        return subprocess.Popen(cmd, cwd=str(PROJECT_ROOT), env=self._child_env())

    # ------------------------------------------------------------------
    # 停止子进程
    # ------------------------------------------------------------------

    def _stop_process(self, proc: Optional[subprocess.Popen], name: str,
                      wait: float = 8.0) -> None:
        if proc is None or proc.poll() is not None:
            return
        logger.info("[capswriterd] 停止 %s (pid=%s)", name, proc.pid)
        try:
            proc.terminate()
            proc.wait(timeout=wait)
        except subprocess.TimeoutExpired:
            logger.warning("[capswriterd] %s 未在 %.0fs 内退出，强制 kill", name, wait)
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
        except Exception as exc:
            logger.warning("[capswriterd] 停止 %s 失败: %s", name, exc)

    def _shutdown(self) -> None:
        """按顺序停止子进程：先 client 后 server。"""
        if self._stopping:
            return
        self._stopping = True
        logger.info("[capswriterd] 正在关闭 ...")
        self._stop_process(self.client, 'client')
        self._stop_process(self.server, 'server')
        logger.info("[capswriterd] 已关闭")

    # ------------------------------------------------------------------
    # 信号处理
    # ------------------------------------------------------------------

    def _handle_signal(self, signum, _frame) -> None:
        logger.info("[capswriterd] 收到信号 %s，开始关闭", signum)
        self._shutdown()
        raise SystemExit(0)

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def run(self) -> int:
        global logger
        logger = _setup_log()

        # 写入 PID 文件
        _write_pid(os.getpid())
        atexit.register(_clear_pid)

        # 注册信号
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._handle_signal)

        logger.info("[capswriterd] 启动，pid=%s，项目根=%s", os.getpid(), PROJECT_ROOT)

        # 1. 启动 server
        self.server = self._start_server()
        logger.info("[capswriterd] server pid=%s，等待就绪 ...", self.server.pid)

        if not _wait_server_ready(timeout=60.0):
            logger.error("[capswriterd] server 在 60 s 内未就绪，中止")
            self._shutdown()
            return 1

        logger.info("[capswriterd] server 已就绪")

        # 2. 启动 client
        self.client = self._start_client()
        logger.info("[capswriterd] client pid=%s", self.client.pid)

        # 3. 监控循环
        try:
            while True:
                time.sleep(2)

                server_dead = self.server.poll() is not None
                client_dead = self.client.poll() is not None

                if server_dead:
                    code = self.server.returncode
                    logger.warning("[capswriterd] server 意外退出 (code=%s)，关闭 client", code)
                    self._shutdown()
                    return 2

                if client_dead:
                    code = self.client.returncode
                    logger.warning("[capswriterd] client 意外退出 (code=%s)，关闭 server", code)
                    self._shutdown()
                    return 3

        except SystemExit:
            return 0
        finally:
            self._shutdown()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_run() -> int:
    """启动守护进程（前台阻塞运行）。"""
    pid = read_pid()
    if is_running(pid):
        print(f"[capswriterd] 已在运行 (pid={pid})")
        return 1
    return CapsWriterDaemon().run()


def cmd_status() -> int:
    """打印当前运行状态，供 capswriter status 调用。"""
    pid = read_pid()
    if is_running(pid):
        print(f"running pid={pid}")
        return 0
    else:
        print("stopped")
        return 1


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 0

    cmd = sys.argv[1]
    if cmd == 'run':
        return cmd_run()
    elif cmd == 'status':
        return cmd_status()
    else:
        print(f"未知子命令: {cmd}")
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
