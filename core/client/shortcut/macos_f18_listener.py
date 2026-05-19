# coding: utf-8
"""
macOS F18 监听器（主动 CGEventTap 实现）。

使用 Quartz CGEventTap 主动拦截 F18 按键事件，并在回调里吞掉该事件，
防止 F18 透传到前台应用（终端等）产生 ^[[32~ 转义序列。

需要 Accessibility 权限（辅助功能），这与自动粘贴共用同一权限。
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import Quartz

from . import logger

# macOS virtual keycode for F18 (kVK_F18 = 0x4F)
_F18_KEYCODE = 0x4F

# CGEventField: kCGKeyboardEventKeycode = 9
_kCGKeyboardEventKeycode = 9


class MacOSF18Listener:
    """
    主动 CGEventTap 实现的 F18 监听器。

    - 监听 keyDown / keyUp
    - 识别到 F18 时调用回调，并返回 None 吞掉事件（终端不再收到 ^[[32~）
    - 非 F18 事件原样透传
    """

    def __init__(
        self,
        on_down: Callable[[], None],
        on_up: Callable[[], None],
        on_tap_failed: Callable[[], None] | None = None,
    ) -> None:
        self._on_down = on_down
        self._on_up = on_up
        # CGEventTap 创建失败时的回调。调用方可在此回调中恢复 hidutil remap，
        # 之后 listener 会改为监听原始 Caps Lock 按键（而非 F18）。
        self._on_tap_failed = on_tap_failed
        self._pressed = False
        self._lock = threading.Lock()
        self._tap = None
        self._run_loop_source = None
        self._thread: threading.Thread | None = None
        self._run_loop = None

        # 保留 callback 引用，防止被 GC 回收（PyObjC 直接接受 Python callable）
        self._callback_ref = self._tap_callback

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def start(self) -> None:
        """启动全局 F18 事件拦截。"""
        if self._tap is not None:
            return

        # 监听 keyDown 和 keyUp
        mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
        )

        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGHIDEventTap,          # 在 HID 层拦截
            Quartz.kCGHeadInsertEventTap,    # 插在最前面
            Quartz.kCGEventTapOptionDefault, # 主动 tap（可吞事件）
            mask,
            self._callback_ref,
            None,
        )

        if self._tap is None:
            logger.warning(
                "[f18-listener] CGEventTap 创建失败，请确认已授权辅助功能（Accessibility）权限。"
            )
            self._start_fallback()
            return

        self._run_loop_source = Quartz.CFMachPortCreateRunLoopSource(
            None, self._tap, 0
        )

        self._thread = threading.Thread(
            target=self._run_loop_thread,
            daemon=True,
            name="F18EventTapThread",
        )
        self._thread.start()
        logger.info("[f18-listener] CGEventTap started (F18 events will be suppressed)")

    def stop(self) -> None:
        """停止事件拦截，释放 tap。"""
        with self._lock:
            self._pressed = False

        if self._tap is not None:
            Quartz.CGEventTapEnable(self._tap, False)
            self._tap = None

        if self._run_loop is not None:
            Quartz.CFRunLoopStop(self._run_loop)
            self._run_loop = None

        logger.info("[f18-listener] stopped")

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _run_loop_thread(self) -> None:
        """在独立线程里跑 CFRunLoop，驱动 CGEventTap 回调。"""
        self._run_loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(
            self._run_loop,
            self._run_loop_source,
            Quartz.kCFRunLoopDefaultMode,
        )
        Quartz.CGEventTapEnable(self._tap, True)
        Quartz.CFRunLoopRun()   # 阻塞，直到 CFRunLoopStop 被调用

    def _tap_callback(self, proxy, event_type, event, _user_info):
        """
        CGEventTap 回调。

        - F18 keyDown：调用 on_down，返回 None 吞掉事件
        - F18 keyUp：调用 on_up，返回 None 吞掉事件
        - 其他事件：原样返回（透传）
        """
        keycode = Quartz.CGEventGetIntegerValueField(event, _kCGKeyboardEventKeycode)

        if keycode != _F18_KEYCODE:
            return event  # 非 F18，透传

        if event_type == Quartz.kCGEventKeyDown:
            with self._lock:
                if self._pressed:
                    return None  # 长按重复触发，吞掉
                self._pressed = True
            logger.info("[f18-listener] F18 down")
            self._on_down()
            return None  # 吞掉，不透传给前台应用

        if event_type == Quartz.kCGEventKeyUp:
            with self._lock:
                if not self._pressed:
                    return None
                self._pressed = False
            logger.info("[f18-listener] F18 up")
            self._on_up()
            return None  # 吞掉

        return event

    # ------------------------------------------------------------------
    # 回退：Accessibility 权限未授权时降级用 pynput
    # ------------------------------------------------------------------

    def _start_fallback(self) -> None:
        """
        Accessibility 未授权时的回退路径。

        有 on_tap_failed 回调时（来自 MacOSCapsF18Bridge）：
          1. 调用回调让上层恢复 hidutil remap（Caps Lock 不再映射到 F18）
          2. 改为被动监听原始 Caps Lock 按键
          3. controller 的 direct_caps_mode 由回调负责开启

        没有回调时（单独使用 listener）：
          降级为监听 F18（旧行为，F18 事件仍会透传到前台应用）。
        """
        if self._on_tap_failed is not None:
            # 通知上层恢复 remap 并切换 controller 到 direct_caps_mode
            self._on_tap_failed()
            self._start_caps_lock_fallback()
        else:
            logger.warning(
                "[f18-listener] 无 on_tap_failed 回调，回退到 F18 pynput 监听"
                "（F18 事件仍会透传，终端内长按 Caps Lock 会出现 ^[[32~ ）。"
            )
            self._start_f18_fallback()

    def _start_caps_lock_fallback(self) -> None:
        """remap 已恢复后，用 pynput 被动监听原始 Caps Lock 按键。"""
        from pynput import keyboard

        def on_press(key):
            if key != keyboard.Key.caps_lock:
                return
            with self._lock:
                if self._pressed:
                    return
                self._pressed = True
            logger.info("[f18-listener] Caps Lock down (direct fallback)")
            self._on_down()

        def on_release(key):
            if key != keyboard.Key.caps_lock:
                return
            with self._lock:
                if not self._pressed:
                    return
                self._pressed = False
            logger.info("[f18-listener] Caps Lock up (direct fallback)")
            self._on_up()

        self._fallback_listener = keyboard.Listener(
            on_press=on_press,
            on_release=on_release,
        )
        self._fallback_listener.start()
        logger.info(
            "[f18-listener] pynput Caps Lock 直接监听已启动（降级模式："
            "短按由 macOS 自然处理，长按触发录音）"
        )

    def _start_f18_fallback(self) -> None:
        """旧行为：remap 保持激活，pynput 被动监听 F18（F18 事件会透传）。"""
        from pynput import keyboard

        def _is_f18(key) -> bool:
            key_name = getattr(key, "name", None)
            return key == getattr(keyboard.Key, "f18", None) or key_name == "f18"

        def on_press(key):
            if not _is_f18(key):
                return
            with self._lock:
                if self._pressed:
                    return
                self._pressed = True
            logger.info("[f18-listener] F18 down (f18 fallback)")
            self._on_down()

        def on_release(key):
            if not _is_f18(key):
                return
            with self._lock:
                if not self._pressed:
                    return
                self._pressed = False
            logger.info("[f18-listener] F18 up (f18 fallback)")
            self._on_up()

        self._fallback_listener = keyboard.Listener(
            on_press=on_press,
            on_release=on_release,
        )
        self._fallback_listener.start()
        logger.info("[f18-listener] pynput F18 fallback started")
