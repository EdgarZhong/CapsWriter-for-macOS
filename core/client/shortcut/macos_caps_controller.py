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
import queue
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
        # 开始/停止在同一业务线程 FIFO 执行：长按成立与 launch() 实际进入之间
        # 若恰好松手，也必须先启动后停止；不能让 stop 被“尚未录音”判断吞掉。
        self._actions = queue.Queue()
        self._action_worker = None
        self._press_id = 0
        self._closed = False  # 退出/撤销接管后，所有迟到的启动动作均失效。

    def _submit_action(self, action: Callable[[], None]) -> None:
        """在控制器锁内登记动作，耗时音频操作只在常驻 daemon 线程执行。"""
        if self._action_worker is None:
            self._action_worker = threading.Thread(
                target=self._run_actions, daemon=True, name='CapsRecordingActions',
            )
            self._action_worker.start()
        self._actions.put(action)

    def _run_actions(self) -> None:
        """异常不能终止消费线程，否则后续正常松手也会永远滞留在队列。"""
        while True:
            action = self._actions.get()
            try:
                if action is None:
                    return
                # 已执行中的启动会在返回后收到 stop；尚未开始的启动/短按直接丢弃。
                if self._closed and action != self._stop_recording:
                    continue
                action()
            except Exception:
                logger.exception('[caps-controller] 按键业务动作失败')
            finally:
                self._actions.task_done()

    def stop(self) -> None:
        """停止接收按键，撤销定时器并为在途启动配对结束动作；不等待原生音频调用。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._press_id += 1
            self._is_down = False
            self._recording_started = False
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if self._action_worker is not None:
                self._submit_action(self._stop_recording)
                self._actions.put(None)

    def on_f18_down(self) -> None:
        """收到 F18 down 后，启动长按判定计时器。"""
        with self._lock:
            if self._closed or self._is_down:
                return

            self._is_down = True
            self._recording_started = False
            self._down_at = time.monotonic()
            self._press_id += 1
            # cancel() 无法撤回已开始执行的 Timer，使用按压代号拒绝旧定时器
            # 在下一次按压中误触发，保持每次都完整经过 200ms 长按判定。
            self._timer = threading.Timer(
                self._hold_threshold_s, self._on_hold_threshold, args=(self._press_id,),
            )
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
            if should_stop:
                self._submit_action(self._stop_recording)
            if should_toggle_caps:
                self._submit_action(self._toggle_caps_lock)

        logger.info(
            "[caps-controller] up duration_ms=%d should_stop=%s should_toggle_caps=%s",
            duration_ms,
            should_stop,
            should_toggle_caps,
        )

        if should_stop:
            logger.info("[caps-controller] long press, stop recording")

        if should_toggle_caps:
            logger.info("[caps-controller] short tap, synthesize CapsLock")

    def _on_hold_threshold(self, press_id: int) -> None:
        """达到长按阈值后正式启动录音。"""
        with self._lock:
            if press_id != self._press_id or not self._is_down or self._recording_started:
                return

            self._recording_started = True
            elapsed_ms = (time.monotonic() - self._down_at) * 1000
            logger.info("[caps-controller] hold threshold reached, start recording elapsed_ms=%.1f",
                        elapsed_ms)
            self._submit_action(self._start_recording)
