# coding: utf-8
"""
CapsWriter Offline 客户端启动入口。

remap 生命周期由 client 自身（app.py / mic_runner.py）管理，
不再经过 MacOSCapsSupervisor 父进程层。
"""
from core.client.main import main as client_main

if __name__ == "__main__":
    raise SystemExit(client_main())
