# coding: utf-8
"""
macOS Caps Lock 合成器。

短按路径需要主动向系统补发一次真实的 `Caps Lock` 切换，
这样 remap 后仍能保留用户原有的大小写锁定手感。
"""

from __future__ import annotations

import time

import Quartz

from . import logger


CAPS_LOCK_KEYCODE = 57


def synthesize_caps_lock_toggle(hold_ms: int = 120) -> None:
    """
    合成一次带短暂按住时长的 `Caps Lock`。

    `Caps Lock` 和普通按键不同，如果 down / up 贴得过近，系统偶尔会忽略这次切换；
    因此这里保留一个可配置的 hold 时间。
    """
    logger.info("[caps-synth] synthesize CapsLock toggle hold_ms=%d", hold_ms)

    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    down = Quartz.CGEventCreateKeyboardEvent(source, CAPS_LOCK_KEYCODE, True)
    up = Quartz.CGEventCreateKeyboardEvent(source, CAPS_LOCK_KEYCODE, False)

    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    time.sleep(hold_ms / 1000.0)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
