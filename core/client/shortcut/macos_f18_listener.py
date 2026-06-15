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
            # TCC 权限被撤销：主动停 RunLoop（不再依赖"会自动退出"的错误假设），
            # 让 _run_loop_thread 走真故障处理（恢复 remap + 引导 + 停）。
            logger.warning("[f18-listener] tap disabled by user input (TCC revoked) – stopping run loop")
            if self._run_loop is not None:
                Quartz.CFRunLoopStop(self._run_loop)
            return None

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

    def _on_timeout(self) -> None:
        """tap 超时被禁用：自救（re-enable + 物理键态对账），并按预算判定是否升级 fatal。"""
        logger.warning("[f18-listener] tap disabled by timeout, re-enabling")
        if self._stopping or self._tap is None:
            return

        # 预算：窗口内 timeout 次数过多，说明不是偶发慢回调 → 升级 fatal
        now = time.monotonic()
        self._timeout_times = [t for t in self._timeout_times if now - t <= _TIMEOUT_BUDGET_WINDOW_S]
        self._timeout_times.append(now)
        if len(self._timeout_times) >= _TIMEOUT_BUDGET_MAX:
            logger.warning(
                "[f18-listener] timeout 预算耗尽（%ds 内 %d 次），升级为 fatal",
                int(_TIMEOUT_BUDGET_WINDOW_S), len(self._timeout_times),
            )
            if self._run_loop is not None:
                Quartz.CFRunLoopStop(self._run_loop)
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
