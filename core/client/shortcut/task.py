# coding: utf-8
"""
快捷键任务模块

管理单个快捷键的录音任务状态
"""

from __future__ import annotations
import asyncio
import platform
import time
from threading import Event, Lock
from typing import TYPE_CHECKING, Optional

from . import logger
from core.tools.my_status import Status
 
if TYPE_CHECKING:
    from core.client.shortcut.shortcut_config import Shortcut
    from core.client.state import ClientState
    from core.client.audio.recorder import AudioRecorder
    from core.client.app import CapsWriterClient



class ShortcutTask:
    """
    单个快捷键的录音任务

    跟踪每个快捷键独立的录音状态，防止互相干扰。
    """

    def __init__(self, app: CapsWriterClient, shortcut: Shortcut, recorder_class=None):
        """
        初始化快捷键任务

        Args:
            app: 客户端 App 实例
            shortcut: 快捷键配置
            recorder_class: AudioRecorder 类（可选，用于延迟导入）
        """
        self.app = app
        self.shortcut = shortcut
        self._recorder_class = recorder_class

        # 任务状态
        self.task: Optional[asyncio.Future] = None
        self.recording_start_time: float = 0.0
        self.is_recording: bool = False
        self.trace_id: Optional[str] = None

        # hold_mode 状态跟踪
        self.pressed: bool = False
        self.released: bool = True
        self.event: Event = Event()

        # 启动/停止生命周期保护：
        # launch() 打开音频流需要数百毫秒，这段时间内 is_recording 仍为 False。
        # 若用户在这个“启动中”窗口内松手，旧逻辑会因 is_recording 为假而丢弃 stop，
        # 导致麦克风流被打开却永不关闭（参见 2026-06-19 Caps 长按竞态故障记录）。
        # 这里用一把锁 + 两个标志补齐“录音启动中 / 待停止”语义，保证只要开流动作
        # 已经开始，松手后就一定存在可达的关闭路径。
        self._lifecycle_lock: Lock = Lock()
        self._launching: bool = False      # launch() 正在打开音频流的窗口内为真
        self._stop_pending: bool = False   # 启动窗口内收到过停止请求

        # 线程池（用于 countdown）
        self.pool = None

        # 录音状态动画
        self._status = Status('开始录音', spinner='point')

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state

    def _get_recorder(self) -> AudioRecorder:
        """获取 AudioRecorder 实例"""
        if self._recorder_class is None:
            from core.client.audio.recorder import AudioRecorder
            self._recorder_class = AudioRecorder
        return self._recorder_class(self.app)

    def launch(self) -> None:
        """启动录音任务"""
        # 并发/重入保护：避免重复启动，并标记进入“启动中”窗口。
        # is_recording 为真表示已在录音；_launching 为真表示另一线程正在开流。
        with self._lifecycle_lock:
            if self.is_recording or self._launching:
                logger.debug(f"[{self.shortcut.key}] 已在录音或正在启动，忽略重复 launch")
                return
            self._launching = True
            self._stop_pending = False

        # 使用“快捷键名 + 纳秒时间戳”构造一次性 trace_id，
        # 便于把按键事件、音频入队、识别任务和最终结果串成同一条时间线。
        self.trace_id = f"{self.shortcut.key}-{time.time_ns()}"
        logger.info(f"[{self.shortcut.key}] 触发：开始录音, trace_id={self.trace_id}")

        # macOS 新路线要求“只在真正录音时占用麦克风”，因此在宣布开始录音前，
        # 先让音频流管理器按需打开输入流。
        # 注意：开流是耗时操作（数百毫秒），刻意放在锁外执行，这样启动期间到来的
        # stop 请求（request_finish）可以无阻塞地登记 _stop_pending，而不是被丢弃。
        if not self.app.stream.start_recording_session():
            logger.error(f"[{self.shortcut.key}] 无法启动录音所需音频流，放弃本次录音")
            with self._lifecycle_lock:
                self._launching = False
                self._stop_pending = False
            return

        # 音频流已打开，正式立起录音状态；同时取出启动期间是否收到过 stop。
        with self._lifecycle_lock:
            self.recording_start_time = time.time()
            self.is_recording = True
            self._launching = False
            stop_pending = self._stop_pending
            self._stop_pending = False

        # 将开始标志放入队列
        asyncio.run_coroutine_threadsafe(
            self.state.queue_in.put({
                'type': 'begin',
                'time': self.recording_start_time,
                'data': None,
                'trace_id': self.trace_id,
                'shortcut_key': self.shortcut.key,
            }),
            self.app.loop
        )

        # 更新录音状态
        self.state.start_recording(
            self.recording_start_time,
            trace_id=self.trace_id,
            shortcut_key=self.shortcut.key,
        )

        # 打印动画：正在录音
        self._status.start()

        # 启动识别任务
        recorder = self._get_recorder()
        self.task = asyncio.run_coroutine_threadsafe(
            recorder.record_and_send(),
            self.app.loop,
        )

        # 关键修复：若在打开音频流期间用户已经松手（启动窗口内收到过 stop），
        # 这里立即走正常收尾，保证麦克风必定被关闭，而不是停留在
        # “麦克风开着却没人来停”的悬挂态。复用 finish() 的标准关闭路径。
        if stop_pending:
            logger.info(f"[{self.shortcut.key}] 启动期间已收到停止请求，立即结束本次录音")
            self.finish()

    def request_finish(self) -> None:
        """请求结束“按住说话”录音（线程安全，且在启动中也安全）。

        与直接调用 finish() 的区别：当任务仍处于“启动中”窗口（音频流尚在打开、
        is_recording 还未置真）时，本方法会登记 _stop_pending 而不是丢弃请求，
        交由 launch() 末尾负责立即收尾，确保麦克风必定被关闭。
        这是修复 Caps 长按竞态（开流已开始但 is_recording 未置真时松手）的入口。
        """
        with self._lifecycle_lock:
            if self._launching:
                self._stop_pending = True
                logger.info(f"[{self.shortcut.key}] 录音启动中收到停止请求，登记待停止")
                return
            if not self.is_recording:
                logger.debug(f"[{self.shortcut.key}] 当前未在录音，忽略停止请求")
                return
        self.finish()

    def cancel(self) -> None:
        """取消录音任务（时间过短）"""
        # 幂等保护：在锁内 check-and-set，避免与 finish()/launch() 收尾路径重复执行。
        with self._lifecycle_lock:
            if not self.is_recording:
                return
            self.is_recording = False

        logger.debug(f"[{self.shortcut.key}] 取消录音任务（时间过短）, trace_id={self.trace_id}")

        self.state.mark_recording_cancel_requested(self.trace_id, time.time())
        self.state.stop_recording()
        self.app.stream.stop_recording_session()
        self._status.stop()

        if self.task is not None:
            self.task.cancel()
        self.task = None

    def finish(self) -> None:
        """完成录音任务"""
        # 幂等保护：在锁内 check-and-set，避免重复 finish（例如 launch() 末尾的
        # stop_pending 收尾与外部 request_finish 同时触发时只生效一次）。
        with self._lifecycle_lock:
            if not self.is_recording:
                return
            self.is_recording = False

        finish_time = time.time()
        logger.info(f"[{self.shortcut.key}] 释放：完成录音, trace_id={self.trace_id}")

        self.state.mark_recording_finish_requested(self.trace_id, finish_time)
        self.state.stop_recording()
        self.app.stream.stop_recording_session()
        self._status.stop()

        asyncio.run_coroutine_threadsafe(
            self.state.queue_in.put({
                'type': 'finish',
                'time': finish_time,
                'data': None,
                'trace_id': self.trace_id,
                'shortcut_key': self.shortcut.key,
            }),
            self.app.loop
        )

        # 是否需要 restore 不再由 Task 自己硬编码平台特判，而是交给管理器统一判断。
        # 这样当 macOS `Caps Lock` 改走原生 HID tap 后，就可以自然地表达：
        # “物理事件已经被底层吞掉，因此长按结束后不应该再补一次 restore”。
        if self._should_restore_after_finish():
            self._restore_key()

    def _should_restore_after_finish(self) -> bool:
        """
        判断当前任务结束后是否需要 restore。

        优先委托给 `ShortcutManager` 做平台级和输入链路级决策；
        只有在管理器缺失时，才回退到原有的兼容逻辑。
        """
        manager = self._manager_ref() if hasattr(self, '_manager_ref') else None
        if manager is not None:
            return manager.should_restore_key_after_finish(self.shortcut.key, self.shortcut)

        return self.shortcut.is_toggle_key() and (
            not self.shortcut.suppress or platform.system() == 'Darwin'
        )

    def _restore_key(self) -> None:
        """恢复按键状态（防自捕获逻辑由 ShortcutManager 处理）"""
        # 通知管理器执行 restore
        # 防自捕获：管理器会设置 flag 再发送按键
        manager = self._manager_ref()
        if manager:
            logger.debug(f"[{self.shortcut.key}] 自动恢复按键状态 (suppress={self.shortcut.suppress})")
            manager.schedule_restore(self.shortcut.key)
        else:
            logger.warning(f"[{self.shortcut.key}] manager 引用丢失，无法 restore")
