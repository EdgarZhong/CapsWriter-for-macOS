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

    # osascript 引导弹窗文案（显示一次后就不再重复）
    _DIALOG_SCRIPT = """\
display dialog "CapsWriter 需要辅助功能权限

请在刚刚打开的「辅助功能」设置中：

• 若列表中已有 CapsWriter
  → 点「−」删除，稍等约 15 秒

• 若列表中没有 CapsWriter
  → 稍等片刻，它将自动出现

看到 CapsWriter 后开启右侧开关即可。
CapsWriter 将自动恢复，无需重启。" ¬
    with title "CapsWriter 需要辅助功能权限" ¬
    buttons {"好的"} default button "好的"\
"""

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
        self._recovering = False    # 防止并发触发多个恢复循环
        self._dialog_shown = False  # 引导弹窗只弹一次

    def start(self) -> None:
        """启动 F18 监听，并同步更新 ErrorBus 的 accessibility_ok 状态。"""
        logger.info("macOS Caps F18 bridge starting")
        self._listener.start()
        # 检查 tap 是否成功建立：_listener._tap 非 None 表示 CGEventTap 创建成功
        eb = getattr(self.app, 'error_bus', None)
        if eb:
            tap_ok = self._listener._tap is not None
            eb.update(accessibility_ok=tap_ok)

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

        # ErrorBus：标记辅助功能不可用
        eb = getattr(self.app, 'error_bus', None)
        if eb:
            eb.update(accessibility_ok=False)

        # 1. 恢复 hidutil remap（Caps Lock 不再映射到 F18）
        if self.app.remap_session is not None:
            try:
                self.app.remap_session.restore()
            except Exception as e:
                logger.warning("[caps-f18-bridge] remap restore failed: %s", e)

        # 2. 自动打开辅助功能设置
        subprocess.Popen([
            'open',
            'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility',
        ])

        # 3. 引导弹窗（只弹一次，分支说明「列表有/无」两种情况）
        if not self._dialog_shown:
            self._dialog_shown = True
            subprocess.Popen(['osascript', '-e', self._DIALOG_SCRIPT])

        # 4. 后台恢复循环：每 15s 重试，权限恢复后自动重建 tap + remap
        threading.Thread(
            target=self._recovery_loop,
            daemon=True,
            name="TapRecoveryThread",
        ).start()

    def _recovery_loop(self) -> None:
        """后台每 15s 轮询重建 CGEventTap，成功后恢复 remap 并发通知。"""
        import time
        logger.info("[caps-f18-bridge] 开始自动恢复循环（每 15s 重试 CGEventTap）")
        while True:
            time.sleep(15)
            if self._listener.restart():
                logger.info("[caps-f18-bridge] CGEventTap 已恢复，重新启用 remap")

                # 恢复 remap（重新激活 Caps Lock → F18 映射）
                if self.app.remap_session is not None:
                    try:
                        self.app.remap_session.start()
                    except Exception as e:
                        logger.warning("[caps-f18-bridge] remap re-enable failed: %s", e)

                # ErrorBus：标记辅助功能已恢复
                eb = getattr(self.app, 'error_bus', None)
                if eb:
                    eb.update(accessibility_ok=True)
                    eb.notify("辅助功能权限已恢复，CapsWriter 运行正常", "accessibility_restored")

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
