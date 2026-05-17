# coding: utf-8
"""
shortcut 子模块

包含快捷键处理相关功能，使用 ShortcutManager 统一管理所有快捷键（键盘和鼠标）。
"""

from .. import logger


def __getattr__(name):
    """
    惰性导出快捷键相关对象。

    旧实现会在包导入阶段立刻加载 `shortcut_manager`，从而把整套监听依赖
    在解释器启动早期就拉起来。macOS 下这会放大平台依赖的初始化副作用，
    也更容易触发循环导入。改成惰性导出后，只有真正访问这些名字时才导入。
    """
    if name in ('Shortcut', 'CommonShortcuts'):
        from core.client.shortcut.shortcut_config import Shortcut, CommonShortcuts
        return {
            'Shortcut': Shortcut,
            'CommonShortcuts': CommonShortcuts,
        }[name]

    if name == 'ShortcutManager':
        from core.client.shortcut.shortcut_manager import ShortcutManager
        return ShortcutManager

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    'logger',
    'Shortcut',
    'CommonShortcuts',
    'ShortcutManager',
]
