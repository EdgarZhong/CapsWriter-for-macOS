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
        self._handled = False       # 真故障只处理一次（防重复弹窗/通知）

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

    def check_health(self) -> None:
        """周期性体检入口（由 5s 心跳调用），委托给底层 F18 listener。

        覆盖「回调不会运行 → 无法自检」的失效（撤辅助功能/回调死锁/撤输入监控），
        是渐进权限引导的触发兜底。详见 `MacOSF18Listener.check_health`。
        """
        self._listener.check_health()

    def _handle_tap_failed(self) -> None:
        """CGEventTap 真故障（创建失败 / 运行中撤权 / RunLoop 退出）的统一处理。

        采用「干净单路径 + 渐进权限引导」UX（见 docs/macos-architecture-decisions.md 第六节）：
        恢复 remap → 标记不可用 + 通知 → 渐进引导（探测辅助功能/输入监控两项权限，逐项引导，
        系统设置单窗口故串行）→ 提示重启。不再静默轮询重建（旧的 15s 自动恢复循环已废弃）。

        本方法已在独立线程（TapFailedCallback）执行，run_guide 内部的轮询/等待阻塞无碍。
        """
        with self._recover_lock:
            if self._handled:
                return  # 已处理过，避免重复弹窗/通知
            self._handled = True

        logger.warning("[caps-f18-bridge] CGEventTap 真故障，恢复键盘并引导用户重授权")

        # 1. 立刻恢复 hidutil remap（Caps Lock 变回普通键，消除"映射着但 tap 已死"的 limbo）
        if self.app.remap_session is not None:
            try:
                self.app.remap_session.restore()
            except Exception as e:
                logger.warning("[caps-f18-bridge] remap restore failed: %s", e)

        # 2. ErrorBus：标记不可用 + 发系统通知（让故障可见，不静默）
        eb = getattr(self.app, 'error_bus', None)
        if eb:
            eb.update(accessibility_ok=False)
            eb.notify(
                "键盘接管已暂停，正在引导你检查辅助功能/输入监控权限",
                "permission_lost",
            )

        # 3. 渐进权限引导：探测两项权限真实状态，只引导未授权的那项，逐项串行
        def _notify(msg: str) -> None:
            if eb:
                # ErrorBus 按 key 做 30s 去重；引导的多条文案集中在 ~50s 内，
                # 必须每条独立 key，否则"✅已就绪"等后续通知会被同 key 吞掉。
                # 按文案内容派生 key：相同文案才去重（如重复的"请拨开关"不刷屏）。
                eb.notify(msg, f"perm_guide_{hash(msg) & 0xffff}")
            else:
                logger.info("[caps-f18-bridge] %s", msg)

        from .macos_permission_guide import run_guide
        run_guide(notify=_notify, dialog=self._show_dialog)

    @staticmethod
    def _show_dialog(body: str, title: str) -> None:
        """渐进引导升级到「删除+重启」时的确认弹窗（非阻塞）。"""
        script = f'display dialog "{body}" with title "{title}" buttons {{"好的"}} default button "好的"'
        subprocess.Popen(['osascript', '-e', script])

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
