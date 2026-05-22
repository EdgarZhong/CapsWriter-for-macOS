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

# 系统禁用 CGEventTap 时发送的特殊事件类型
_kCGEventTapDisabledByTimeout   = 0xFFFFFFFE  # tap 回调超时，可尝试重新启用
_kCGEventTapDisabledByUserInput = 0xFFFFFFFF  # TCC 权限被撤销，重新启用无效


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
        # CGEventTap 不可用（启动失败或运行时被撤销）时的回调
        self._on_tap_failed = on_tap_failed
        self._pressed = False
        self._lock = threading.Lock()
        self._tap = None
        self._run_loop_source = None
        self._thread: threading.Thread | None = None
        self._run_loop = None
        self._stopping = False  # True 表示主动 stop()，用于区分意外退出

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
        self._stopping = True  # 先置标志，避免 _run_loop_thread 误判为意外退出
        with self._lock:
            self._pressed = False

        if self._tap is not None:
            Quartz.CGEventTapEnable(self._tap, False)
            self._tap = None

        if self._run_loop is not None:
            Quartz.CFRunLoopStop(self._run_loop)
            self._run_loop = None

        logger.info("[f18-listener] stopped")

    def restart(self) -> bool:
        """尝试重建 CGEventTap。供恢复循环调用，成功返回 True。"""
        # 清理残留状态
        if self._run_loop is not None:
            Quartz.CFRunLoopStop(self._run_loop)
            self._run_loop = None

        self._stopping = False
        self._tap = None
        self._run_loop_source = None
        with self._lock:
            self._pressed = False

        mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
        )
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGHIDEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,
            mask,
            self._callback_ref,
            None,
        )
        if self._tap is None:
            return False

        self._run_loop_source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        self._thread = threading.Thread(
            target=self._run_loop_thread,
            daemon=True,
            name="F18EventTapThread",
        )
        self._thread.start()
        logger.info("[f18-listener] CGEventTap restarted successfully")
        return True

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
        Quartz.CFRunLoopRun()   # 阻塞，直到 CFRunLoopStop 被调用或 tap 被系统撤销

        # CFRunLoop 退出：若非主动 stop()，说明 tap 被系统撤销（TCC 权限收回）
        if not self._stopping and self._on_tap_failed is not None:
            logger.warning("[f18-listener] CFRunLoop exited unexpectedly – tap likely revoked by TCC")
            self._tap = None
            self._run_loop_source = None
            self._run_loop = None
            threading.Thread(
                target=self._on_tap_failed,
                daemon=True,
                name="TapFailedCallback",
            ).start()

    def _tap_callback(self, proxy, event_type, event, _user_info):
        """
        CGEventTap 回调。

        - F18 keyDown：调用 on_down，返回 None 吞掉事件
        - F18 keyUp：调用 on_up，返回 None 吞掉事件
        - 其他事件：原样返回（透传）
        - Disabled 事件：尝试重新启用（超时）或忽略（TCC 撤销，RunLoop 退出后由线程处理）
        """
        # 处理 tap 被系统禁用的特殊事件（event 参数在此情况下为 NULL，不能访问）
        if event_type == _kCGEventTapDisabledByTimeout:
            logger.warning("[f18-listener] tap disabled by timeout, re-enabling")
            if self._tap is not None and not self._stopping:
                Quartz.CGEventTapEnable(self._tap, True)
            return None
        if event_type == _kCGEventTapDisabledByUserInput:
            # TCC 权限被撤销；不尝试重新启用（会失败）
            # RunLoop 在此之后会自动退出，由 _run_loop_thread 的 _on_tap_failed 处理
            logger.warning("[f18-listener] tap disabled by user input (TCC revoked)")
            return None

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
        """CGEventTap 不可用：通知上层（恢复 remap、通知用户、启动恢复循环），不再静默降级。"""
        if self._on_tap_failed is not None:
            self._on_tap_failed()
        else:
            logger.error(
                "[f18-listener] CGEventTap 不可用且无 on_tap_failed 回调，"
                "Caps Lock 监听已停止工作。请授权辅助功能（Accessibility）权限后重启。"
            )

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
