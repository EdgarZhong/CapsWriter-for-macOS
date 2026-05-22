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

    # client PID 文件（macOS .app 模式下由 start_client_macos.py 写入）
    CLIENT_PID_FILE = STATE_DIR / 'client.pid'

    def __init__(self) -> None:
        self.server: Optional[subprocess.Popen] = None
        # macOS .app 模式：client 指向 `open -W` 的 Popen（用于检测退出）
        # 普通模式：client 指向 Python 子进程的 Popen
        self.client: Optional[subprocess.Popen] = None
        # macOS .app 模式下，实际 client Python 进程的 PID（用于发送 SIGTERM）
        self._client_pid: Optional[int] = None
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

    def _start_server(self) -> subprocess.Popen:
        cmd = [self._python(), str(PROJECT_ROOT / 'start_server.py')]
        logger.info("[capswriterd] 启动 server: %s", cmd)
        return subprocess.Popen(cmd, cwd=str(PROJECT_ROOT))

    def _start_client(self) -> subprocess.Popen:
        app_path = PROJECT_ROOT / 'CapsWriter.app'
        if app_path.exists() and sys.platform == 'darwin':
            return self._start_client_app(app_path)
        # 非 macOS 或 .app 不存在时回退到直接启动
        cmd = [self._python(), str(PROJECT_ROOT / 'start_client.py')]
        logger.info("[capswriterd] 启动 client: %s", cmd)
        return subprocess.Popen(cmd, cwd=str(PROJECT_ROOT))

    def _start_client_app(self, app_path: Path) -> subprocess.Popen:
        """通过 macOS `open` 命令启动 CapsWriter.app，赋予 client GUI 应用身份。"""
        cmd = ['open', '-W', '-n', str(app_path)]
        logger.info("[capswriterd] 启动 client (.app): %s", cmd)
        proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT))

        # 等待 client 写入 PID 文件（最多 10 秒）
        deadline = time.time() + 10.0
        while time.time() < deadline:
            pid = self._read_client_pid()
            if pid is not None and is_running(pid):
                self._client_pid = pid
                logger.info("[capswriterd] client .app 实际 PID=%s", pid)
                return proc
            time.sleep(0.5)

        logger.warning("[capswriterd] 未能在 10s 内读取到 client PID 文件")
        return proc

    def _read_client_pid(self) -> Optional[int]:
        """读取 client PID 文件。"""
        try:
            return int(self.CLIENT_PID_FILE.read_text().strip())
        except Exception:
            return None

    # ------------------------------------------------------------------
    # 停止子进程
    # ------------------------------------------------------------------

    def _stop_client(self, wait: float = 8.0) -> None:
        """停止 client 进程（适配 .app 和普通模式）。"""
        # macOS .app 模式：向实际 client Python 进程发送 SIGTERM
        if self._client_pid is not None:
            logger.info("[capswriterd] 停止 client (pid=%s, .app 模式)", self._client_pid)
            try:
                os.kill(self._client_pid, signal.SIGTERM)
            except ProcessLookupError:
                logger.info("[capswriterd] client pid=%s 已不存在", self._client_pid)
                return
            except Exception as exc:
                logger.warning("[capswriterd] 向 client pid=%s 发送 SIGTERM 失败: %s",
                               self._client_pid, exc)
                return

            # 等待 client 退出
            deadline = time.time() + wait
            while time.time() < deadline:
                if not is_running(self._client_pid):
                    logger.info("[capswriterd] client 已退出")
                    self._client_pid = None
                    return
                time.sleep(0.3)

            # 超时强制 kill
            logger.warning("[capswriterd] client 未在 %.0fs 内退出，强制 kill", wait)
            try:
                os.kill(self._client_pid, signal.SIGKILL)
            except Exception:
                pass
            self._client_pid = None
            return

        # 普通模式：使用 Popen.terminate()
        self._stop_process(self.client, 'client', wait)

    def _stop_process(self, proc: Optional[subprocess.Popen], name: str,
                      wait: float = 8.0) -> None:
        """停止普通子进程（server 或非 .app 模式的 client）。"""
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
        self._stop_client()
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

                # client 存活检测：.app 模式看实际 PID，普通模式看 Popen
                if self._client_pid is not None:
                    client_dead = not is_running(self._client_pid)
                else:
                    client_dead = self.client.poll() is not None

                if server_dead:
                    code = self.server.returncode
                    logger.warning("[capswriterd] server 意外退出 (code=%s)，关闭 client", code)
                    self._shutdown()
                    return 2

                if client_dead:
                    logger.warning("[capswriterd] client 意外退出，关闭 server")
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
