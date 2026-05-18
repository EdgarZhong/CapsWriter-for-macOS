# coding: utf-8
"""
macOS `Caps Lock -> F18` 业务桥接器。

它把三层职责粘合起来：
1. `MacOSF18Listener`：监听 remap 后的 F18；
2. `MacOSCapsController`：做短按/长按判定；
3. `ShortcutManager`：复用现有录音任务和结果链路。
"""

from __future__ import annotations

from config_client import ClientConfig as Config

from . import logger
from .macos_caps_controller import MacOSCapsController
from .macos_caps_state import toggle_caps_lock_state
from .macos_f18_listener import MacOSF18Listener


class MacOSCapsF18Bridge:
    """在应用内部桥接 macOS 新版 Caps Lock 交互。"""

    def __init__(self, app) -> None:
        self.app = app
        self._listener = MacOSF18Listener(
            on_down=self._on_down,
            on_up=self._on_up,
        )
        self._controller = MacOSCapsController(
            start_recording=self._start_recording,
            stop_recording=self._stop_recording,
            toggle_caps_lock=self._toggle_caps_lock,
            hold_threshold_ms=Config.macos_caps_hold_threshold_ms,
        )

    def start(self) -> None:
        """启动 F18 监听。"""
        logger.info("macOS Caps F18 bridge starting")
        self._listener.start()

    def stop(self) -> None:
        """停止 F18 监听。"""
        logger.info("macOS Caps F18 bridge stopping")
        self._listener.stop()

    def _on_down(self) -> None:
        """把 F18 down 转交给短按/长按控制器。"""
        self._controller.on_f18_down()

    def _on_up(self) -> None:
        """把 F18 up 转交给短按/长按控制器。"""
        self._controller.on_f18_up()

    def _start_recording(self) -> None:
        """长按成立后，复用现有 `caps_lock` 录音任务。"""
        self.app.shortcut.start_press_to_talk("caps_lock")

    def _stop_recording(self) -> None:
        """长按结束后，复用现有 `caps_lock` 结束录音路径。"""
        self.app.shortcut.stop_press_to_talk("caps_lock")

    @staticmethod
    def _toggle_caps_lock() -> None:
        """
        短按路径：通过 IOKit 直接切换 Caps Lock 状态。

        为什么不能用 CGEventPost 合成 Caps Lock 事件？
          hidutil remap 在 HID 状态机之前拦截了 keycode，物理按键不会触发
          Caps Lock 状态切换；CGEventPost 合成的键盘事件同样无法触发状态机。

        IOKit 的 IOHIDSetModifierLockState 直接修改 HID 系统内部状态，
        绕开事件管道，不受 remap 影响，也不需要 Accessibility 权限。
        """
        logger.info("[caps-f18-bridge] short press: toggling CapsLock via IOKit")
        toggle_caps_lock_state()
