# coding: utf-8
from __future__ import annotations

import os
import platform
import sys

from config_client import ClientConfig as Config


def _has_file_mode_args(argv: list[str]) -> bool:
    """
    判断当前是否是文件转录模式。

    文件模式不需要接管 `Caps Lock`，因此也不应进入 macOS supervisor。
    """
    return any(os.path.exists(arg) for arg in argv)


def main() -> int:
    """根据平台和运行模式选择直接启动或进入 macOS supervisor。"""
    argv = sys.argv[1:]

    if (
        platform.system() == "Darwin"
        and getattr(Config, "macos_caps_mode", "off") == "remap_f18"
        and getattr(Config, "macos_caps_remap_enabled", True)
        and os.environ.get("CAPSWRITER_MACOS_SUPERVISOR_CHILD") != "1"
        and not _has_file_mode_args(argv)
    ):
        from core.client.launcher.macos_caps_supervisor import main as supervisor_main

        return supervisor_main(argv)

    from core.client.main import main as client_main

    return client_main()


if __name__ == "__main__":
    raise SystemExit(main())
