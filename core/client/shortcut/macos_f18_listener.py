# coding: utf-8
"""
macOS F18 监听器（主动 CGEventTap 实现）。

使用 Quartz CGEventTap 主动拦截 F18 按键事件，并在回调里吞掉该事件，
防止 F18 透传到前台应用（终端等）产生 ^[[32~ 转义序列。

需要 Accessibility 权限（辅助功能），这与自动粘贴共用同一权限。

设计要点（详见 docs/macos-architecture-decisions.md 第六节）：
- **回调非阻塞铁律**：tap 回调只判 keycode、吞掉 F18、把 down/up 投递到工作线程队列，
  绝不在回调里跑业务（start/stop recording）。否则回调阻塞 → 系统挂起全局键盘 → 冻结。
- **失败分类**（判据：re-enable 同一个 tap 能否恢复）：
  · DisabledByTimeout：回调太慢被临时禁用 → 自救（re-enable + 物理键态对账），带预算；
  · 状态失稳（禁用窗口内丢了 keyUp）：用 CGEventSourceKeyState 对账，补投 up 自愈；
  · DisabledByUserInput / 创建失败 / RunLoop 退出：真故障 → 回调上层（恢复 remap + 引导 + 停）。
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable

import Quartz

from . import logger

# macOS virtual keycode for F18 (kVK_F18 = 0x4F)
_F18_KEYCODE = 0x4F

# CGEventField: kCGKeyboardEventKeycode = 9
_kCGKeyboardEventKeycode = 9

# 系统禁用 CGEventTap 时发送的特殊事件类型
_kCGEventTapDisabledByTimeout   = 0xFFFFFFFE  # tap 回调超时，可尝试重新启用（自救）
_kCGEventTapDisabledByUserInput = 0xFFFFFFFF  # TCC 权限被撤销，重新启用无效（真故障）

# timeout 自救预算：窗口内 timeout 次数达到阈值则升级为 fatal，避免无限静默打转
_TIMEOUT_BUDGET_WINDOW_S = 5.0
_TIMEOUT_BUDGET_MAX = 3

# ---- 回调心跳 + 单次启动校验 ping（真理判据）----
# tap「真活」的唯一可靠证据是事件能不能流过回调；CGEventTapIsEnabled 会被 cdhash 失效的
# stale 死 tap 骗（句柄非空、enabled，却收不到任何事件）。
# 口径（option C）：合成 ping 只用于「单次启动校验」（start() 后打一发确诊本次 tap 真活/stale）；
# 运行期体检仍用便宜可靠的 CGEventTapIsEnabled——stale 主要发生在重建后下次启动，已被启动校验覆盖。
_HEARTBEAT_FRESH_S = 3.0        # 最近这么久内有真实事件 → 直接判活（快路径，免合成探测）
_PROBE_INTERVAL_S = 0.3         # 合成 ping 每次等待回声的间隔
_PROBE_TOTAL_TIMEOUT_S = 1.5    # 启动校验总超时（含 RunLoop 刚起时漏第一发的竞态重试窗口）
# 合成 ping 用 kCGEventSourceUserData 字段打标，回调据此识别并吞掉，绝不当真实 F18。
_kCGEventSourceUserData = 42
_PING_USERDATA = 0x43575F50     # "CW_P"


class MacOSF18Listener:
    """
    主动 CGEventTap 实现的 F18 监听器。

    - 监听 keyDown / keyUp，识别到 F18 时吞掉事件（终端不再收到 ^[[32~）
    - 业务回调（on_down / on_up）在独立工作线程执行，tap 回调本身保持 O(1) 不阻塞
    """

    def __init__(
        self,
        on_down: Callable[[], None],
        on_up: Callable[[], None],
        on_tap_failed: Callable[[], None] | None = None,
    ) -> None:
        self._on_down = on_down
        self._on_up = on_up
        # CGEventTap 不可用（创建失败 / 运行时撤权 / RunLoop 退出）时的真故障回调
        self._on_tap_failed = on_tap_failed
        self._pressed = False
        self._lock = threading.Lock()
        self._tap = None
        self._run_loop_source = None
        self._thread: threading.Thread | None = None
        self._run_loop = None
        self._stopping = False  # True 表示主动 stop()，用于区分意外退出

        # 保留 callback 引用，防止被 GC 回收（PyObjC 直接接受 Python callable）
        self._callback_ref = self._tap_callback

        # 业务分发：tap 回调只入队，工作线程消费，保证回调绝不阻塞输入管道
        self._event_queue: queue.Queue[str] = queue.Queue()
        self._worker_thread: threading.Thread | None = None
        self._worker_started = False

        # timeout 自救预算（最近若干次 timeout 的时间戳）
        self._timeout_times: list[float] = []

        # 回调心跳：最近一次「真实事件」流过回调的时刻（合成探测不计入）。
        # tap 是否「真活」的唯一可靠证据是事件能不能流过回调——CGEventTapIsEnabled
        # 会被 cdhash 失效的 stale 死 tap 骗（句柄非空、enabled，却收不到任何事件）。
        self._last_event_ts = 0.0
        # 单次启动校验 ping：_probe_alive() 发打标 F18，回调收到该 ping 即 set 此事件
        self._ping_event = threading.Event()

        # 我们「意图」让 tap 处于启用态吗？周期性体检 check_health() 据此判断：
        # 意图启用(True) 但系统报告已禁用 ⇒ 系统在背后禁了它(撤权/超时/回调死锁) ⇒ 兜底 fatal。
        # 我们自己主动禁用时置 False，避免体检误判。
        self._tap_should_be_enabled = False

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def start(self) -> None:
        """启动全局 F18 事件拦截。"""
        if self._tap is not None:
            return

        self._ensure_worker()

        self._tap = self._create_tap()
        if self._tap is None:
            logger.warning(
                "[f18-listener] CGEventTap 创建失败，请确认已授权辅助功能（Accessibility）权限。"
            )
            self._handle_tap_unavailable()  # 真故障：启动即无权限
            return

        self._run_loop_source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        self._thread = threading.Thread(
            target=self._run_loop_thread,
            daemon=True,
            name="F18EventTapThread",
        )
        self._thread.start()
        logger.info("[f18-listener] CGEventTap started (F18 events will be suppressed)")

    def stop(self) -> None:
        """停止事件拦截，释放 tap。"""
        self._stopping = True  # 先置标志，避免 _run_loop_thread 误判为意外退出
        self._tap_should_be_enabled = False  # 主动停止，守护线程不再守护
        with self._lock:
            self._pressed = False

        if self._tap is not None:
            Quartz.CGEventTapEnable(self._tap, False)
            self._tap = None

        if self._run_loop is not None:
            Quartz.CFRunLoopStop(self._run_loop)
            self._run_loop = None

        logger.info("[f18-listener] stopped")

    # ------------------------------------------------------------------
    # 内部：tap 创建
    # ------------------------------------------------------------------

    def _create_tap(self):
        """创建主动型 CGEventTap（HID 层、头插、可吞事件），失败返回 None。"""
        mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
        )
        return Quartz.CGEventTapCreate(
            Quartz.kCGHIDEventTap,            # 在 HID 层拦截
            Quartz.kCGHeadInsertEventTap,     # 插在最前面
            Quartz.kCGEventTapOptionDefault,  # 主动 tap（可吞事件）
            mask,
            self._callback_ref,
            None,
        )

    def attempt_im_registration(self) -> None:
        """仅为触发「输入监控」条目注册而尝试创建一次 tap，随即丢弃。

        「输入监控」条目唯一可靠的注册手段 = **在辅助功能已就绪的前提下尝试创建
        CGEventTap**（详见 docs/macos-architecture-decisions.md 第六节；逻辑反证见该节）。
        本方法不持有这个 tap、不起 RunLoop，只取其「把本 app 写进输入监控列表」的副作用；
        真正长期运行的 tap 仍由 start() 在用户拨开输入监控开关并重启后建立。

        调用前提：辅助功能已授权（否则尝试会在写表前失败，不产生注册副作用——
        这正是基线「开完辅助功能、输入监控条目还出不来」的真因）。
        """
        try:
            tap = self._create_tap()
        except Exception as e:
            logger.warning("[f18-listener] 输入监控条目注册尝试异常: %s", e)
            return
        if tap is not None:
            # 拿到句柄也不留用：本次只为注册副作用，立刻禁用丢弃，交给 GC。
            try:
                Quartz.CGEventTapEnable(tap, False)
            except Exception:
                pass
        logger.info("[f18-listener] 已尝试创建 tap 以触发输入监控条目注册（随即丢弃）")

    # ------------------------------------------------------------------
    # 内部：业务分发工作线程（保证 tap 回调不阻塞）
    # ------------------------------------------------------------------

    def _ensure_worker(self) -> None:
        if self._worker_started:
            return
        self._worker_started = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="F18DispatchThread",
        )
        self._worker_thread.start()

    def _worker_loop(self) -> None:
        """消费 down / up 事件，在工作线程跑业务（start/stop recording），与 tap 回调彻底解耦。

        即便业务里有慢操作（收尾音频、发 websocket），也只会让后续事件在队列里排队，
        绝不会阻塞 tap 回调本身 → 系统永远不会因为我们而挂起键盘。
        """
        while True:
            item = self._event_queue.get()
            try:
                if item == 'down':
                    self._on_down()
                elif item == 'up':
                    self._on_up()
            except Exception as e:
                logger.warning("[f18-listener] 业务回调异常: %s", e)

    # ------------------------------------------------------------------
    # 内部：RunLoop
    # ------------------------------------------------------------------

    def _run_loop_thread(self) -> None:
        """在独立线程里跑 CFRunLoop，驱动 CGEventTap 回调。"""
        self._run_loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(
            self._run_loop,
            self._run_loop_source,
            Quartz.kCFRunLoopDefaultMode,
        )
        Quartz.CGEventTapEnable(self._tap, True)
        self._tap_should_be_enabled = True   # 进入「意图启用」态，守护线程开始守护
        Quartz.CFRunLoopRun()   # 阻塞，直到 CFRunLoopStop 或 tap 被作废

        # CFRunLoop 退出：若非主动 stop()，即真故障（撤权 / timeout 预算耗尽 / tap 作废）
        if not self._stopping:
            logger.warning("[f18-listener] CFRunLoop exited – tap unavailable, escalating to fatal")
            self._tap = None
            self._run_loop_source = None
            self._run_loop = None
            self._handle_tap_unavailable()

    # ------------------------------------------------------------------
    # 内部：tap 回调（必须保持 O(1) 不阻塞）
    # ------------------------------------------------------------------

    def _tap_callback(self, proxy, event_type, event, _user_info):
        """CGEventTap 回调：识别 F18 → 吞掉并入队；禁用事件 → 按分类自救或上报。"""
        # 处理 tap 被系统禁用的特殊事件（此情况下 event 参数为 NULL，不能访问）
        if event_type == _kCGEventTapDisabledByTimeout:
            self._on_timeout()
            return None
        if event_type == _kCGEventTapDisabledByUserInput:
            # TCC 权限被撤销（少数场景走这里）：统一走 _go_fatal —— 先禁 tap 放行键盘，
            # 再停 RunLoop 触发真故障处理（恢复 remap + 引导）。
            self._go_fatal("tap disabled by user input (TCC revoked)")
            return None

        # 合成存活探测 ping：识别打标事件 → 吞掉、置位，绝不计入心跳、不当真实按键。
        try:
            if Quartz.CGEventGetIntegerValueField(event, _kCGEventSourceUserData) == _PING_USERDATA:
                self._ping_event.set()
                return None
        except Exception:
            pass

        # 回调心跳：真实事件流过回调即更新时刻——tap「真活」的唯一可靠证据。
        self._last_event_ts = time.monotonic()

        keycode = Quartz.CGEventGetIntegerValueField(event, _kCGKeyboardEventKeycode)
        if keycode != _F18_KEYCODE:
            return event  # 非 F18，透传

        if event_type == Quartz.kCGEventKeyDown:
            with self._lock:
                if self._pressed:
                    return None  # 长按重复触发，吞掉
                self._pressed = True
            self._event_queue.put('down')  # 业务甩到工作线程，回调立即返回
            return None

        if event_type == Quartz.kCGEventKeyUp:
            with self._lock:
                if not self._pressed:
                    return None
                self._pressed = False
            self._event_queue.put('up')
            return None

        return event

    def _probe_alive(self, total_timeout: float = _PROBE_TOTAL_TIMEOUT_S) -> bool:
        """主动存活探测：发打标的合成 F18，看回调是否在超时内收到回声。

        用于**单次启动校验**——区分 tap 是真活，还是「句柄非空却已 stale 死」
        （后者 CGEventTapIsEnabled 抓不到）。安全性：tap 健康时 ping 会被回调吞掉
        （不泄漏、不触发录音）；tap 已死时这一发会泄漏一个 F18，但死 tap 下真实 Caps→F18
        本就在泄漏，多一发无妨——换来的是能确诊这条静默死法。
        必须在 RunLoop 运行、tap 已建的前提下调用。

        含竞态重试：RunLoop 刚起时可能漏掉第一发，故在总超时内每隔 _PROBE_INTERVAL_S 重发。
        """
        if self._tap is None:
            return False
        try:
            self._ping_event.clear()
            src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
            deadline = time.monotonic() + total_timeout
            while time.monotonic() < deadline:
                # 一对 down/up，避免留下「卡住的键」
                for is_down in (True, False):
                    ev = Quartz.CGEventCreateKeyboardEvent(src, _F18_KEYCODE, is_down)
                    if ev is not None:
                        Quartz.CGEventSetIntegerValueField(
                            ev, _kCGEventSourceUserData, _PING_USERDATA
                        )
                        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
                if self._ping_event.wait(_PROBE_INTERVAL_S):
                    return True
            return False
        except Exception as e:
            logger.warning("[f18-listener] 存活探测异常: %s", e)
            return False

    def tap_healthy(self) -> bool:
        """tap 是否真能工作（真理判据）。供 bridge 启动校验 / 引导用。

        判定层次：① `_tap` 非空且系统报告 enabled（便宜的黑盒前置）；
        ② 最近有真实事件流过 → 直接判活（快路径）；
        ③ 否则发合成 ping 确诊（区分「空闲」与「stale 死 tap」）。
        `_tap` 为 None（创建失败 / 已 fatal 收掉）时直接 False。
        """
        if self._tap is None:
            return False
        try:
            if not Quartz.CGEventTapIsEnabled(self._tap):
                return False
        except Exception:
            return False
        if time.monotonic() - self._last_event_ts <= _HEARTBEAT_FRESH_S:
            return True
        return self._probe_alive()

    @staticmethod
    def _ax_is_trusted() -> bool:
        """当前进程是否仍有辅助功能权限（撤权判据）。探测失败时不误判（返回 True）。"""
        try:
            from ApplicationServices import AXIsProcessTrusted
            return bool(AXIsProcessTrusted())
        except Exception:
            return True

    def _go_fatal(self, reason: str) -> None:
        """升级真故障：**第一时间禁用 active tap 放行键盘**（解冻关键），再停 RunLoop 走恢复链路。

        为什么必须先禁用 tap：`kCGHIDEventTap` 的 active tap 一旦「装着但进程已无权服务」，
        系统会把键盘事件全部扣在该 tap 处等待处理 → 全局键盘冻结。`CGEventTapEnable(False)`
        立即让事件正常透传，这才是解冻动作；之后 CFRunLoopStop 触发 _handle_tap_unavailable
        （恢复 remap + 引导）。
        """
        logger.warning("[f18-listener] 升级 fatal：%s", reason)
        self._tap_should_be_enabled = False  # 主动禁用，守护线程别再重复触发
        try:
            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, False)  # 立刻放行键盘，消除冻结
        except Exception as e:
            logger.warning("[f18-listener] 禁用 tap 失败: %s", e)
        if self._run_loop is not None:
            Quartz.CFRunLoopStop(self._run_loop)

    def check_health(self) -> None:
        """周期性外部体检（由 5s 心跳 `mic_runner._heartbeat_task` 调用，**非独立线程**）。

        **仅凭 tap 的外部可观测状态判健康，不信任回调/业务的任何自我汇报。**
        它覆盖的正是「回调不会运行 → 无法自检」的失效（循环无法观测自己的「没在执行」）：

        - **回调死锁 / 撤辅助功能**：系统在背后禁用 tap ⇒ `CGEventTapIsEnabled(tap)==False`
          （由系统维护的黑盒事实）。

        发现异常即 `_go_fatal`（放行键盘 + 恢复 remap + 引导），**绝不 re-enable**。
        注意：键盘「不冻结」并不依赖本体检（由系统超时窗 + `_on_timeout` 不盲目 re-enable 保证），
        本体检只做善后/检测，故放在 5s 心跳上足矣、无需专线程。

        重要口径收敛：
        - 这里**不再**把 `IOHIDCheckAccess` 作为 fatal 判据。
        - 最新实测表明，「输入监控」的面板显示状态与 active tap 的实时可用性并不稳定等价；
          若继续把它当硬性故障条件，会把首次冷启动与部分重启场景误导到输入监控页。
        """
        if self._stopping or self._tap is None:
            return
        if not self._tap_should_be_enabled:
            return  # 意图禁用态（启动中/正在 fatal/stop），不体检
        try:
            enabled = Quartz.CGEventTapIsEnabled(self._tap)
        except Exception:
            return
        if not enabled:
            logger.warning(
                "[f18-listener] 体检: 意图启用但系统已禁用 tap"
                "（撤辅助功能/超时/回调死锁），升级 fatal"
            )
            self._go_fatal("健康检查: tap 被系统禁用")

    def _on_timeout(self) -> None:
        """tap 超时被禁用：区分「撤权」与「偶发慢回调」。

        **关键修复（实测）**：macOS 上运行时撤销辅助功能权限，表现为 `DisabledByTimeout`
        （**不是** `DisabledByUserInput`）。若此刻已失去 trust，再 re-enable 只会让一个
        「装着但已死」的 active tap 继续扣留键盘 → 永久冻结，且往往只来一次 timeout、预算
        根本来不及升级。因此先查 trust：失去权限即立即升级 fatal（禁用 tap + 恢复），不 re-enable。
        """
        if self._stopping or self._tap is None:
            return

        # 撤权判据：失去辅助功能权限 → 不是偶发慢回调，立即升级 fatal（别再 re-enable 死 tap）
        if not self._ax_is_trusted():
            self._go_fatal("timeout 且已失去辅助功能权限（撤权）")
            return

        # 真·偶发慢回调（回调已 O(1)，这里应极罕见）：自救（re-enable + 物理键态对账），带预算
        logger.warning("[f18-listener] tap disabled by timeout, re-enabling")
        now = time.monotonic()
        self._timeout_times = [t for t in self._timeout_times if now - t <= _TIMEOUT_BUDGET_WINDOW_S]
        self._timeout_times.append(now)
        if len(self._timeout_times) >= _TIMEOUT_BUDGET_MAX:
            self._go_fatal(
                f"timeout 预算耗尽（{int(_TIMEOUT_BUDGET_WINDOW_S)}s 内 {len(self._timeout_times)} 次）"
            )
            return

        Quartz.CGEventTapEnable(self._tap, True)
        self._reconcile_press_state()

    def _reconcile_press_state(self) -> None:
        """对账内存态与物理键态。

        tap 被禁用的窗口内可能丢失了 keyUp，导致 _pressed（及上层 _is_down）永久卡在按下，
        表现为"松手后录音停不下、之后短按长按全失灵"。这里查物理 F18 键态：
        若内存认为按下、物理已松开 → 补投一个 up，停掉卡住的录音、清状态。
        """
        try:
            physically_down = Quartz.CGEventSourceKeyState(
                Quartz.kCGEventSourceStateHIDSystemState, _F18_KEYCODE
            )
        except Exception:
            return
        with self._lock:
            stuck = self._pressed and not physically_down
            if stuck:
                self._pressed = False
        if stuck:
            logger.warning("[f18-listener] 检测到丢失的 keyUp（_pressed=True 但物理已松开），补投 up 自愈")
            self._event_queue.put('up')

    # ------------------------------------------------------------------
    # 内部：真故障上报（创建失败 / 撤权 / RunLoop 退出，统一走上层 fatal 单路径）
    # ------------------------------------------------------------------

    def _handle_tap_unavailable(self) -> None:
        """CGEventTap 不可用：上报上层（恢复 remap、引导用户重授权后重启），不再静默降级。"""
        if self._on_tap_failed is not None:
            # 在独立线程回调，避免占用 RunLoop 线程
            threading.Thread(
                target=self._on_tap_failed,
                daemon=True,
                name="TapFailedCallback",
            ).start()
        else:
            logger.error(
                "[f18-listener] CGEventTap 不可用且无 on_tap_failed 回调，"
                "Caps Lock 监听已停止。请授权辅助功能（Accessibility）后重启。"
            )
