# coding: utf-8
"""
客户端直接入口。

这个模块只负责启动真正的客户端主进程，不承担 macOS `Caps Lock`
映射管理职责。这样外层 supervisor 才能安全地把“父进程负责 remap 生命周期、
子进程负责业务运行”这两个层级分开。
"""

from __future__ import annotations

from core.client import CapsWriterClient


def main() -> int:
    """启动客户端并返回进程退出码。"""
    CapsWriterClient().start()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
