# coding: utf-8
"""
macOS Caps Lock 短按/长按控制器。

这里不直接关心底层是物理 `Caps Lock` 还是 remap 后的 `F18`，
只消费一对稳定的 down / up 语义，并在中间做：
1. 短按：补发一次系统 `Caps Lock`；
2. 长按：开始录音，松手结束录音。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from . import logger


class MacOSCapsController:
    """负责把 F18 / Caps Lock 的 down / up 事件翻译成短按切换与长按录音。"""

    def __init__(
        self,
        start_recording: Callable[[], None],
        stop_recording: Callable[[], None],
        toggle_caps_lock: Callable[[], None],
        hold_threshold_ms: int = 200,
    ) -> None:
        self._start_recording = start_recording
        self._stop_recording = stop_recording
        self._toggle_caps_lock = toggle_caps_lock
        self._hold_threshold_s = hold_threshold_ms / 1000.0

        self._lock = threading.RLock()
        self._is_down = False
        self._recording_started = False
        self._down_at: float | None = None
        self._timer: threading.Timer | None = None

    def on_f18_down(self) -> None:
        """收到 F18 down 后，启动长按判定计时器。"""
        with self._lock:
            if self._is_down:
                return

            self._is_down = True
            self._recording_started = False
            self._down_at = time.monotonic()
            self._timer = threading.Timer(self._hold_threshold_s, self._on_hold_threshold)
            self._timer.daemon = True
            self._timer.start()

        logger.info("[caps-controller] down threshold_ms=%d", int(self._hold_threshold_s * 1000))

    def on_f18_up(self) -> None:
        """收到 F18 up 后，根据当前状态决定短按切换或长按停止录音。"""
        should_stop = False
        should_toggle_caps = False
        duration_ms = 0

        with self._lock:
            if not self._is_down:
                return

            now = time.monotonic()
            if self._down_at is not None:
                duration_ms = int((now - self._down_at) * 1000)

            self._is_down = False

            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

            if self._recording_started:
                should_stop = True
            else:
                should_toggle_caps = True

            self._recording_started = False
            self._down_at = None

        logger.info(
            "[caps-controller] up duration_ms=%d should_stop=%s should_toggle_caps=%s",
            duration_ms,
            should_stop,
            should_toggle_caps,
        )

        if should_stop:
            logger.info("[caps-controller] long press, stop recording")
            self._stop_recording()

        if should_toggle_caps:
            logger.info("[caps-controller] short tap, synthesize CapsLock")
            self._toggle_caps_lock()

    def _on_hold_threshold(self) -> None:
        """达到长按阈值后正式启动录音。"""
        with self._lock:
            if not self._is_down or self._recording_started:
                return

            self._recording_started = True

        logger.info("[caps-controller] hold threshold reached, start recording")
        self._start_recording()
