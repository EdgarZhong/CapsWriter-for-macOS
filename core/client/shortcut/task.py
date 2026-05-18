# coding: utf-8
"""
快捷键任务模块

管理单个快捷键的录音任务状态
"""

from __future__ import annotations
import asyncio
import platform
import time
from threading import Event
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
        # 使用“快捷键名 + 纳秒时间戳”构造一次性 trace_id，
        # 便于把按键事件、音频入队、识别任务和最终结果串成同一条时间线。
        self.trace_id = f"{self.shortcut.key}-{time.time_ns()}"
        logger.info(f"[{self.shortcut.key}] 触发：开始录音, trace_id={self.trace_id}")

        # macOS 新路线要求“只在真正录音时占用麦克风”，因此在宣布开始录音前，
        # 先让音频流管理器按需打开输入流。
        if not self.app.stream.start_recording_session():
            logger.error(f"[{self.shortcut.key}] 无法启动录音所需音频流，放弃本次录音")
            return

        # 记录开始时间
        self.recording_start_time = time.time()
        self.is_recording = True

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

    def cancel(self) -> None:
        """取消录音任务（时间过短）"""
        logger.debug(f"[{self.shortcut.key}] 取消录音任务（时间过短）, trace_id={self.trace_id}")

        self.is_recording = False
        self.state.mark_recording_cancel_requested(self.trace_id, time.time())
        self.state.stop_recording()
        self.app.stream.stop_recording_session()
        self._status.stop()

        self.task.cancel()
        self.task = None

    def finish(self) -> None:
        """完成录音任务"""
        finish_time = time.time()
        logger.info(f"[{self.shortcut.key}] 释放：完成录音, trace_id={self.trace_id}")

        self.is_recording = False
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
