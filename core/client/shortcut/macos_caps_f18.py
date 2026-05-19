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
        self._controller = MacOSCapsController(
            start_recording=self._start_recording,
            stop_recording=self._stop_recording,
            toggle_caps_lock=self._toggle_caps_lock,
            hold_threshold_ms=Config.macos_caps_hold_threshold_ms,
        )
        self._listener = MacOSF18Listener(
            on_down=self._on_down,
            on_up=self._on_up,
            on_tap_failed=self._handle_tap_failed,
        )

    def start(self) -> None:
        """启动 F18 监听。"""
        logger.info("macOS Caps F18 bridge starting")
        self._listener.start()

    def stop(self) -> None:
        """停止 F18 监听。"""
        logger.info("macOS Caps F18 bridge stopping")
        self._listener.stop()

    def _handle_tap_failed(self) -> None:
        """
        CGEventTap 创建失败时的回调。

        执行顺序：
        1. 恢复 hidutil remap（Caps Lock 不再映射到 F18，停止波浪线透传）
        2. 把 controller 切换到 direct_caps_mode（短按跳过 IOKit 切换，
           长按在录音前用 IOKit 撤销 macOS 的自动状态切换）
        """
        logger.warning(
            "[caps-f18-bridge] CGEventTap 不可用（缺少 Accessibility 权限）。"
            "正在恢复 hidutil remap 并切换到 Caps Lock 直接监听模式。"
            "请前往 系统设置 → 隐私与安全性 → 辅助功能，"
            "将运行 client 的 Python 或终端 App 添加到列表后重启 client。"
        )
        if self.app.remap_session is not None:
            try:
                self.app.remap_session.restore()
            except Exception as e:
                logger.warning("[caps-f18-bridge] remap restore failed: %s", e)
        # 切换控制器到直接 Caps Lock 模式
        self._controller.direct_caps_mode = True

    def _on_down(self) -> None:
        """把 F18 / Caps Lock down 转交给短按/长按控制器。"""
        self._controller.on_f18_down()

    def _on_up(self) -> None:
        """把 F18 / Caps Lock up 转交给短按/长按控制器。"""
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
