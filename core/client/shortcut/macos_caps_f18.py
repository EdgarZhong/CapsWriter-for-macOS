# coding: utf-8
"""
macOS `Caps Lock -> F18` 业务桥接器。

它把三层职责粘合起来：
1. `MacOSF18Listener`：监听 remap 后的 F18；
2. `MacOSCapsController`：做短按/长按判定；
3. `ShortcutManager`：复用现有录音任务和结果链路。
"""

from __future__ import annotations

import threading

from config_client import ClientConfig as Config

from . import logger
from .macos_caps_controller import MacOSCapsController
from .macos_caps_state import toggle_caps_lock_state
from .macos_f18_listener import MacOSF18Listener
from .macos_permission_guide import PermPhase, check_accessibility, run_guide


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
        """启动 F18 监听 + 单次启动校验（option C，详见 docs 第六节）。

        「tap 建起来了」不等于「tap 真活」——cdhash 失效的 stale 死 tap 同样非 NULL。
        所以建好后发一发合成 ping 确诊（tap_healthy）；真活才算就绪，否则收掉死 tap 转引导。
        """
        logger.info("macOS Caps F18 bridge starting")

        # 顺序铁律：辅助功能先行（docs 第六节）。AX 未就绪时**绝不预先创建 tap**——
        # `CGEventTapCreate` 本身会触发「输入监控」TCC 弹窗并把 IM 条目注册进列表；
        # 若它抢在 AX 弹窗之前冒出来，用户会先看到输入监控窗、顺手开了就重启 → AX 仍缺、
        # 行为不可预测。故 AX 缺失时直接进引导（run_guide 内先弹 AX），待 AX 就绪后才由
        # try_register_im 补一次 tap 尝试去注册/弹 IM——保证「辅助功能先弹、输入监控后弹」。
        # check_accessibility() 是只读的 AXIsProcessTrusted，无弹窗、无副作用，可安全前置。
        if not check_accessibility():
            logger.warning(
                "[caps-f18-bridge] 辅助功能未就绪，先引导 AX（不预创建 tap，避免抢先弹输入监控）"
            )
            threading.Thread(
                target=self._handle_tap_failed,
                daemon=True,
                name="PermGuideThread",
            ).start()
            return

        self._listener.start()

        if self._listener._tap is None:
            # 创建失败（缺权限）：listener 已在 _handle_tap_unavailable 里触发了
            # on_tap_failed → _handle_tap_failed（独立线程在跑引导），这里不重复调用。
            logger.warning("[caps-f18-bridge] tap 未建立，已交由权限引导接管")
            return

        # 单次启动校验：非 NULL 仍要 ping 确诊真活（防 stale 死 tap 被误当就绪）
        if self._listener.tap_healthy():
            self._mark_ready()
            return

        # 非 NULL 却 ping 无回声 = 疑似 stale 死 tap：收掉它、通知用户 reset-permissions
        logger.warning("[caps-f18-bridge] tap 建起但启动校验无回声（疑 stale），建议 reset-permissions")
        self._listener.stop()
        eb = getattr(self.app, 'error_bus', None)
        if eb:
            # 同 _handle_tap_failed：显式置 error 让菜单栏圆点转红，不停在绿。
            eb.update(state='error', accessibility_ok=False)
            eb.notify(
                "键盘接管启动校验未通过（权限可能失效），"
                "请运行 capswriter reset-permissions 后重新启动",
                "stale_tap",
            )

    def _mark_ready(self) -> None:
        """启动校验通过：键盘接管真正就绪，更新 ErrorBus。"""
        logger.info("[caps-f18-bridge] 键盘接管已就绪（启动校验通过）")
        eb = getattr(self.app, 'error_bus', None)
        if eb:
            eb.update(accessibility_ok=True)
        self._report_phase(PermPhase.READY)

    def _report_phase(self, phase: PermPhase) -> None:
        """把权限引导的全局时效状态上报给 ErrorBus（→ status.json / 菜单栏 / CLI）。"""
        eb = getattr(self.app, 'error_bus', None)
        if eb:
            eb.update(perm_phase=phase.value)

    def is_tap_available(self) -> bool:
        """返回当前键盘接管是否真的已建立。

        这里故意不看「辅助功能」布尔值，而是直接看底层 listener 是否持有已创建的 tap。
        原因：最近实测表明，TCC / 系统设置里的权限显示状态与「本次启动是否已经成功建起
        active CGEventTap」不是一回事；只有 tap 真建起来，才能把客户端视为“键盘已就绪”。
        """
        return self._listener._tap is not None

    def stop(self) -> None:
        """停止 F18 监听。"""
        logger.info("macOS Caps F18 bridge stopping")
        self._listener.stop()

    def check_health(self) -> None:
        """周期性体检入口（由 5s 心跳调用），委托给底层 F18 listener。

        覆盖「回调不会运行 → 无法自检」的失效（撤辅助功能/回调死锁），
        是渐进权限引导的触发兜底。详见 `MacOSF18Listener.check_health`。
        """
        self._listener.check_health()

    def _handle_tap_failed(self) -> None:
        """CGEventTap 真故障 / 未就绪的统一处理：恢复键盘 → 通知 → 跑引导状态机。

        遵循 docs 第六节（2026-06-22 重订）：**绝不退出进程**（杜绝 fatal→退出→KeepAlive
        的 13s 死循环）。恢复 remap 放行键盘后，进程原地存活、跑权限引导；用户按指引补齐
        权限并重启客户端后，由全新会话从头重探重建。

        本方法已在独立线程执行（listener 的 TapFailedCallback，或 start() 的 stale 分支），
        run_guide 内部的轮询/等待阻塞无碍主线程。
        """
        with self._recover_lock:
            if self._handled:
                return  # 已处理过，避免重复弹窗/通知
            self._handled = True

        logger.warning("[caps-f18-bridge] CGEventTap 真故障/未就绪，恢复键盘并引导用户")

        # 1. 立刻恢复 hidutil remap（Caps Lock 变回普通键，消除"映射着但 tap 已死"的 limbo）
        if self.app.remap_session is not None:
            try:
                self.app.remap_session.restore()
            except Exception as e:
                logger.warning("[caps-f18-bridge] remap restore failed: %s", e)

        # 2. ErrorBus：标记不可用 + 发系统通知（让故障可见，不静默）
        eb = getattr(self.app, 'error_bus', None)
        if eb:
            # state='error' 让菜单栏圆点立刻转红「键盘接管/权限未就绪」。
            # result_processor 只在连接状态变化时才用 is_tap_available() 重算 state，
            # 运行中撤权不触发它，故必须在此显式置 error，否则圆点会一直停在 ready（绿）
            # 误导用户「以为还在正常工作」。
            eb.update(state='error', accessibility_ok=False)
            eb.notify(
                "键盘接管已暂停，正在引导你检查键盘权限",
                "permission_lost",
            )

        def _notify(msg: str) -> None:
            if eb:
                # ErrorBus 按 key 做 30s 去重；引导多条文案集中在数十秒内，必须每条独立 key，
                # 否则后续"✅已就绪"等会被同 key 吞掉。按文案内容派生 key：相同文案才去重。
                eb.notify(msg, f"perm_guide_{hash(msg) & 0xffff}")
            else:
                logger.info("[caps-f18-bridge] %s", msg)

        # 3. 跑权限引导状态机：辅助功能先行 → AX 就绪后补一次 tap 尝试注册 IM 条目 → 引导 IM；
        #    引导只说「打开开关」和「请重启」，绝不说「删条目」。
        run_guide(
            notify=_notify,
            try_register_im=self._listener.attempt_im_registration,
            on_phase=self._report_phase,
        )
        # run_guide 返回后**不退出进程**：保持存活，等用户补齐权限并重启客户端。

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
