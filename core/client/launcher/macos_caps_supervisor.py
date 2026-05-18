# coding: utf-8
"""
macOS Caps Lock supervisor。

职责划分：
1. 父进程负责 `hidutil` 映射的启用与恢复；
2. 子进程负责真正运行客户端；
3. 当子进程退出或父进程收到终止信号时，优先恢复原始键位映射。
"""

from __future__ import annotations

import atexit
import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Optional

from core.client.shortcut.macos_caps_remap import MacOSCapsRemapSession


logger = logging.getLogger("capswriter.macos_caps_supervisor")


def _configure_logging() -> None:
    """为 supervisor 单独初始化最小日志输出。"""
    if logger.handlers:
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


class MacOSCapsSupervisor:
    """
    macOS 客户端父进程。

    这个实现借鉴了旧 `custom` 分支“父进程托管子进程”的控制层级，
    但只保留与本轮目标相关的最小职责，不再迁入旧的性能/省电模式切换。
    """

    def __init__(self, argv: list[str]) -> None:
        self.argv = list(argv)
        self.project_root = Path(__file__).resolve().parents[3]
        self.child: Optional[subprocess.Popen] = None
        self.remap_session = MacOSCapsRemapSession()
        self._restored = False

    def _build_child_env(self) -> dict[str, str]:
        """构造子进程环境变量，避免入口脚本再次递归进入 supervisor。"""
        env = os.environ.copy()
        env["CAPSWRITER_MACOS_SUPERVISOR_CHILD"] = "1"
        return env

    def _restore(self) -> None:
        """恢复原始键位映射，且保证整个生命周期内只执行一次。"""
        if self._restored:
            return

        self._restored = True
        try:
            self.remap_session.restore()
        except Exception:
            logger.exception("[caps-supervisor] restore failed")

    def _terminate_child(self) -> None:
        """在父进程退出时尽量温和地结束子进程。"""
        if self.child is None or self.child.poll() is not None:
            return

        logger.warning("[caps-supervisor] terminating child process")
        try:
            self.child.terminate()
            self.child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("[caps-supervisor] child did not exit after SIGTERM, killing")
            try:
                self.child.kill()
                self.child.wait(timeout=5)
            except Exception:
                logger.exception("[caps-supervisor] kill child failed")
        except Exception:
            logger.exception("[caps-supervisor] terminate child failed unexpectedly")

    def _handle_signal(self, signum, frame) -> None:
        """父进程收到终止信号时，先停子进程，再恢复映射。"""
        logger.warning("[caps-supervisor] received signal=%s", signum)
        self._terminate_child()
        self._restore()
        raise SystemExit(128 + int(signum))

    def run(self) -> int:
        """启动 remap，拉起客户端子进程，并等待其退出。"""
        _configure_logging()
        atexit.register(self._restore)

        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, self._handle_signal)

        self.remap_session.start()

        cmd = [sys.executable, "-m", "core.client.main", *self.argv]
        logger.info("[caps-supervisor] starting child: %s", cmd)
        self.child = subprocess.Popen(
            cmd,
            cwd=str(self.project_root),
            env=self._build_child_env(),
        )

        try:
            return self.child.wait()
        finally:
            logger.info("[caps-supervisor] child exited, restoring remap")
            self._restore()


def main(argv: list[str] | None = None) -> int:
    """supervisor 模块命令行入口。"""
    return MacOSCapsSupervisor(sys.argv[1:] if argv is None else argv).run()


if __name__ == "__main__":
    raise SystemExit(main())
