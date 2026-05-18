# coding: utf-8
"""
macOS F18 监听器。

运行期把物理 `Caps Lock` remap 成 `F18` 后，客户端就不再和“锁定键语义”打交道，
而是像处理普通功能键一样处理一对稳定的 `keyDown / keyUp`。
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from pynput import keyboard

from . import logger


class MacOSF18Listener:
    """监听 remap 后的 F18 按下/抬起事件。"""

    def __init__(self, on_down: Callable[[], None], on_up: Callable[[], None]) -> None:
        self._on_down = on_down
        self._on_up = on_up
        self._listener: keyboard.Listener | None = None
        self._pressed = False
        self._lock = threading.Lock()

    @staticmethod
    def _is_f18(key) -> bool:
        """
        判断当前按键是否为 F18。

        `pynput` 在不同版本下既可能给出 `keyboard.Key.f18`，也可能只暴露带
        `name` 属性的对象，因此这里做双保险判断。
        """
        key_name = getattr(key, "name", None)
        return key == getattr(keyboard.Key, "f18", None) or key_name == "f18"

    def start(self) -> None:
        """启动全局监听。"""
        if self._listener is not None:
            return

        self._listener = keyboard.Listener(
            on_press=self._handle_press,
            on_release=self._handle_release,
        )
        self._listener.start()
        logger.info("[f18-listener] started")

    def stop(self) -> None:
        """停止全局监听并重置内部按下状态。"""
        listener = self._listener
        self._listener = None

        with self._lock:
            self._pressed = False

        if listener is not None:
            listener.stop()
            logger.info("[f18-listener] stopped")

    def _handle_press(self, key) -> None:
        """仅在 F18 首次按下时向上层发出 down 事件。"""
        if not self._is_f18(key):
            return

        with self._lock:
            if self._pressed:
                return
            self._pressed = True

        logger.info("[f18-listener] F18 down")
        self._on_down()

    def _handle_release(self, key) -> None:
        """仅在 F18 首次释放时向上层发出 up 事件。"""
        if not self._is_f18(key):
            return

        with self._lock:
            if not self._pressed:
                return
            self._pressed = False

        logger.info("[f18-listener] F18 up")
        self._on_up()
