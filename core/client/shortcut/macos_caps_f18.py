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
from .macos_caps_synth import synthesize_caps_lock_toggle
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
        """短按路径通过合成 `Caps Lock` 保留系统切换语义。"""
        synthesize_caps_lock_toggle(Config.macos_caps_synth_caps_hold_ms)
