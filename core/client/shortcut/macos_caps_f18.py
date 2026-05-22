# coding: utf-8
"""
macOS `Caps Lock -> F18` 业务桥接器。

它把三层职责粘合起来：
1. `MacOSF18Listener`：监听 remap 后的 F18；
2. `MacOSCapsController`：做短按/长按判定；
3. `ShortcutManager`：复用现有录音任务和结果链路。
"""

from __future__ import annotations

import subprocess
import threading

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
        self._recover_lock = threading.Lock()
        self._recovering = False  # 防止并发触发多个恢复循环

    def start(self) -> None:
        """启动 F18 监听。"""
        logger.info("macOS Caps F18 bridge starting")
        self._listener.start()

    def stop(self) -> None:
        """停止 F18 监听。"""
        logger.info("macOS Caps F18 bridge stopping")
        self._listener.stop()

    def _handle_tap_failed(self) -> None:
        """CGEventTap 失效（启动失败或运行时被 TCC 撤销）的统一处理。"""
        with self._recover_lock:
            if self._recovering:
                return  # 恢复循环已在运行，不重复触发
            self._recovering = True

        logger.warning("[caps-f18-bridge] CGEventTap 失效，开始恢复流程")

        # 1. 恢复 hidutil remap（Caps Lock 不再映射到 F18）
        if self.app.remap_session is not None:
            try:
                self.app.remap_session.restore()
            except Exception as e:
                logger.warning("[caps-f18-bridge] remap restore failed: %s", e)

        # 2. 系统通知 + 自动打开辅助功能设置，引导用户授权
        subprocess.Popen(
            ['osascript', '-e',
             'display notification "请在弹出的设置中重新授权 CapsWriter，授权后将自动恢复" '
             'with title "CapsWriter 需要辅助功能权限"'],
        )
        subprocess.Popen([
            'open',
            'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility',
        ])

        # 3. 后台恢复循环：每10秒重试，权限恢复后自动重建 tap + remap
        threading.Thread(
            target=self._recovery_loop,
            daemon=True,
            name="TapRecoveryThread",
        ).start()

    def _recovery_loop(self) -> None:
        """后台轮询重建 CGEventTap，成功后恢复 remap 并通知用户。"""
        import time
        logger.info("[caps-f18-bridge] 开始自动恢复循环（每10秒重试 CGEventTap）")
        while True:
            time.sleep(10)
            if self._listener.restart():
                logger.info("[caps-f18-bridge] CGEventTap 已恢复，重新启用 remap")
                if self.app.remap_session is not None:
                    try:
                        self.app.remap_session.start()
                    except Exception as e:
                        logger.warning("[caps-f18-bridge] remap re-enable failed: %s", e)
                subprocess.Popen(
                    ['osascript', '-e',
                     'display notification "Caps Lock 录音功能已自动恢复" with title "CapsWriter"'],
                )
                with self._recover_lock:
                    self._recovering = False
                break

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
