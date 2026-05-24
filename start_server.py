# coding: utf-8
import os
import signal
from multiprocessing import freeze_support
from core.server.app import CapsWriterServer


def _on_sigterm(signum, frame):
    # launchd stop 发来 SIGTERM → 立即以 exit 0 退出
    # 使用 os._exit 避免 asyncio 事件循环干扰，确保 launchd 看到 exit 0 而不重启
    os._exit(0)


if __name__ == '__main__':
    freeze_support()
    signal.signal(signal.SIGTERM, _on_sigterm)
    CapsWriterServer().start()
