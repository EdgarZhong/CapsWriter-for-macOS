# coding: utf-8
"""
macOS Caps Lock 合成器。

短按路径需要主动向系统补发一次真实的 `Caps Lock` 切换，
这样 remap 后仍能保留用户原有的大小写锁定手感。

关键问题：hidutil 的 Caps->F18 remap 是全局 HID 层拦截。
若直接在 kCGHIDEventTap 层合成 Caps Lock CGEvent，该事件会被
hidutil 再次截获并转为 F18，导致 MacOSF18Listener 误触发一轮新的录音周期。

解法：合成前临时从 hidutil 里去掉 Caps->F18 条目，合成结束后再恢复。
短暂窗口（约 20ms + hold_ms + 20ms）内物理 Caps Lock 会直通，但概率极低可接受。
"""

from __future__ import annotations

import time

import Quartz

from . import logger
from .macos_caps_remap import (
    CAPS_LOCK_HID,
    get_user_key_mapping,
    set_user_key_mapping,
)


CAPS_LOCK_KEYCODE = 57

# hidutil 接受新映射到实际生效之间的最小等待时间（实测约 10ms 足够）
_REMAP_SETTLE_S = 0.02


def synthesize_caps_lock_toggle(hold_ms: int = 120) -> None:
    """
    合成一次带短暂按住时长的 `Caps Lock`。

    `Caps Lock` 和普通按键不同，如果 down / up 贴得过近，系统偶尔会忽略这次切换；
    因此这里保留一个可配置的 hold 时间。

    流程：
    1. 读取当前 hidutil UserKeyMapping；
    2. 去掉 Caps->F18 条目，临时写回（让 Caps Lock 恢复直通）；
    3. 等待 hidutil 生效（_REMAP_SETTLE_S）；
    4. 合成 Caps Lock down / up；
    5. 等待事件处理完毕；
    6. 恢复含 Caps->F18 的原映射。
    """
    logger.info("[caps-synth] synthesize CapsLock toggle hold_ms=%d", hold_ms)

    # 读取当前映射（通常含 Caps->F18 条目）
    current_mapping = get_user_key_mapping()

    # 构建不含 Caps Lock 源映射的临时映射
    mapping_without_caps = [
        entry for entry in current_mapping
        if int(entry.get("HIDKeyboardModifierMappingSrc", -1)) != CAPS_LOCK_HID
    ]

    try:
        # 临时去掉 Caps->F18，让合成事件能直通 HID 层
        set_user_key_mapping(mapping_without_caps)
        logger.debug("[caps-synth] temporarily removed Caps->F18 remap")
        time.sleep(_REMAP_SETTLE_S)

        # 合成 Caps Lock 事件
        source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
        down = Quartz.CGEventCreateKeyboardEvent(source, CAPS_LOCK_KEYCODE, True)
        up = Quartz.CGEventCreateKeyboardEvent(source, CAPS_LOCK_KEYCODE, False)

        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        time.sleep(hold_ms / 1000.0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)

        # 等待事件处理完毕再恢复 remap，避免恢复过早导致 up 事件被重新拦截
        time.sleep(_REMAP_SETTLE_S)
        logger.debug("[caps-synth] CapsLock event sent")

    finally:
        # 无论是否异常，都恢复 Caps->F18 remap
        set_user_key_mapping(current_mapping)
        logger.debug("[caps-synth] restored Caps->F18 remap")
