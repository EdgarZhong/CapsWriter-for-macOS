# coding: utf-8
"""
客户端状态管理模块

提供 ClientState 类用于管理客户端的全局状态。
使用 dataclass 提供类型安全和清晰的状态定义。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Dict, Any

if TYPE_CHECKING:
    import sounddevice as sd
    from websockets.legacy.client import WebSocketClientProtocol
    from .app import CapsWriterClient

from rich.console import Console
from rich.theme import Theme

from . import logger


# 配置 Rich console
_theme = Theme({
    'markdown.code': 'cyan',
    'markdown.item.number': 'yellow'
})
console = Console(highlight=False, soft_wrap=True, theme=_theme)


@dataclass
class ClientState:
    """
    客户端运行状态

    管理客户端运行过程中的所有共享状态，包括事件循环、消息队列、
    WebSocket 连接、音频流和录音状态等。

    Attributes:
        loop: asyncio 事件循环
        queue_in: 音频数据输入队列
        queue_out: 处理结果输出队列（保留）
        websocket: WebSocket 客户端连接
        stream: 音频输入流
        recording: 是否正在录音
        recording_start_time: 录音开始时间戳
        audio_files: 任务ID到音频文件路径的映射
        last_recognition_text: 最近一次识别的最终文本（热词替换后），供"添加纠错记录"使用
    """

    queue_in: asyncio.Queue = field(default_factory=asyncio.Queue)
    queue_out: asyncio.Queue = field(default_factory=asyncio.Queue)
    websocket: Optional[WebSocketClientProtocol] = None
    stream: Optional[sd.InputStream] = None
    app: Optional[CapsWriterClient] = None

    recording: bool = False
    recording_start_time: float = 0.0
    audio_files: Dict[str, Path] = field(default_factory=dict)
    active_trace_id: Optional[str] = None
    trace_contexts: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    task_trace_map: Dict[str, str] = field(default_factory=dict)
    first_audio_logged_trace_ids: set[str] = field(default_factory=set)

    # 最近一次识别结果（用于手动添加纠错记录）
    last_recognition_text: Optional[str] = None
    
    # 最近一次输出内容（如果是 LLM 润色，则是润色结果；否则是原始识别结果）
    last_output_text: Optional[str] = None
    

    
    def reset(self) -> None:
        """
        重置状态
        
        清理所有状态，关闭连接和流。用于重新初始化或退出时清理。
        """
        logger.debug("正在重置客户端状态...")
        
        # 关闭 WebSocket 连接
        ws = self.websocket
        if ws is not None:
            try:
                if not ws.closed and self.app and self.app.loop and self.app.loop.is_running():
                    asyncio.run_coroutine_threadsafe(ws.close(), self.app.loop)
            except Exception:
                pass
            self.websocket = None
        
        # 关闭音频流
        if self.stream is not None:
            try:
                self.stream.close()
                logger.debug("音频流已关闭")
            except Exception:
                pass
            self.stream = None
        
        # 重置其他状态
        self.recording = False
        self.recording_start_time = 0.0
        self.audio_files.clear()
        self.active_trace_id = None
        self.trace_contexts.clear()
        self.task_trace_map.clear()
        self.first_audio_logged_trace_ids.clear()
        
        logger.debug("客户端状态重置完成")
    
    def start_recording(
        self,
        start_time: float,
        trace_id: Optional[str] = None,
        shortcut_key: Optional[str] = None,
    ) -> None:
        """
        开始录音
        
        Args:
            start_time: 录音开始的时间戳
            trace_id: 本次按键驱动链路的追踪标识
            shortcut_key: 触发本次录音的快捷键名
        """
        self.recording = True
        self.recording_start_time = start_time
        self.active_trace_id = trace_id

        if trace_id:
            self.trace_contexts[trace_id] = {
                'trace_id': trace_id,
                'shortcut_key': shortcut_key,
                'recording_start_time': start_time,
                'finish_requested_time': None,
                'cancel_requested_time': None,
                'first_audio_enqueue_time': None,
                'first_audio_frames': None,
                'audio_metric_count': 0,
                'first_audio_rms': None,
                'first_audio_peak': None,
                'first_audio_mean_abs': None,
                'first_audio_zero_ratio': None,
                'task_id': None,
            }

        logger.debug(
            "录音状态已更新: "
            f"recording=True, start_time={start_time:.2f}, trace_id={trace_id}, shortcut_key={shortcut_key}"
        )
    
    def stop_recording(self) -> float:
        """
        停止录音
        
        Returns:
            录音持续时间（秒）
        """
        duration = 0.0
        if self.recording_start_time > 0:
            duration = time.time() - self.recording_start_time
        
        self.recording = False
        self.recording_start_time = 0.0
        self.active_trace_id = None
        logger.debug(f"录音状态已更新: recording=False, duration={duration:.2f}s")
        return duration

    def mark_recording_finish_requested(self, trace_id: Optional[str], finish_time: float) -> None:
        """
        记录“按键抬起后请求结束录音”的时间。

        这个时间点很关键：它表示录音链路是否真的被当前这次按键释放驱动结束。
        """
        if not trace_id:
            return

        context = self.trace_contexts.get(trace_id)
        if context is None:
            return

        context['finish_requested_time'] = finish_time
        logger.info(
            f"[trace {trace_id}] 已记录结束请求时间: finish_requested_time={finish_time:.6f}"
        )

    def mark_recording_cancel_requested(self, trace_id: Optional[str], cancel_time: float) -> None:
        """
        记录“按键过短，录音任务被取消”的时间。

        后续如果要排查“短按误触发录音”或“取消后仍有识别结果”，这个点位能直接对账。
        """
        if not trace_id:
            return

        context = self.trace_contexts.get(trace_id)
        if context is None:
            return

        context['cancel_requested_time'] = cancel_time
        logger.info(
            f"[trace {trace_id}] 已记录取消请求时间: cancel_requested_time={cancel_time:.6f}"
        )

    def mark_first_audio_enqueue(
        self,
        trace_id: Optional[str],
        enqueue_time: float,
        frames: int,
    ) -> None:
        """
        记录第一次音频帧真正进入队列的时间。

        这能区分：
        1. 只是状态机认为“开始录音”；
        2. 麦克风音频确实已经开始往识别链路里流动。
        """
        if not trace_id or trace_id in self.first_audio_logged_trace_ids:
            return

        context = self.trace_contexts.get(trace_id)
        if context is None:
            return

        context['first_audio_enqueue_time'] = enqueue_time
        context['first_audio_frames'] = frames
        self.first_audio_logged_trace_ids.add(trace_id)
        logger.info(
            f"[trace {trace_id}] 首帧音频已入队: enqueue_time={enqueue_time:.6f}, frames={frames}"
        )

    def mark_audio_metrics(
        self,
        trace_id: Optional[str],
        rms: float,
        peak: float,
        mean_abs: float,
        zero_ratio: float,
        channels: int,
    ) -> None:
        """
        记录录音前几帧的音频能量指标。

        这些指标的目标不是做声学分析，而是快速回答两个排查问题：
        1. 当前录音链路里拿到的是不是全零/近零静音帧。
        2. 用户说话时输入幅值有没有明显变化。
        """
        if not trace_id:
            return

        context = self.trace_contexts.get(trace_id)
        if context is None:
            return

        metric_count = int(context.get('audio_metric_count', 0)) + 1
        context['audio_metric_count'] = metric_count

        if metric_count == 1:
            context['first_audio_rms'] = rms
            context['first_audio_peak'] = peak
            context['first_audio_mean_abs'] = mean_abs
            context['first_audio_zero_ratio'] = zero_ratio

        # 只打印前 5 帧，避免长录音时把日志刷爆。
        if metric_count <= 5:
            logger.info(
                f"[trace {trace_id}] 音频能量样本#{metric_count}: "
                f"rms={rms:.6f}, peak={peak:.6f}, mean_abs={mean_abs:.6f}, "
                f"zero_ratio={zero_ratio:.4f}, channels={channels}"
            )

    def bind_task_trace(self, task_id: str, trace_id: Optional[str]) -> None:
        """
        把服务端识别任务 ID 绑定到本次按键追踪链路。

        这样最终识别结果回到客户端时，就能回溯到具体是哪次 `Caps Lock`
        触发了它，而不是只看到一个孤立的服务端任务号。
        """
        if not trace_id:
            return

        context = self.trace_contexts.get(trace_id)
        if context is None:
            return

        context['task_id'] = task_id
        self.task_trace_map[task_id] = trace_id
        logger.info(f"[trace {trace_id}] 已绑定识别任务: task_id={task_id}")

    def pop_trace_context_by_task_id(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        依据识别任务 ID 取回并移除追踪上下文。

        最终结果落地后，这条追踪链路就完成了，直接在这里回收上下文，避免残留状态持续堆积。
        """
        trace_id = self.task_trace_map.pop(task_id, None)
        if trace_id is None:
            return None

        self.first_audio_logged_trace_ids.discard(trace_id)
        return self.trace_contexts.pop(trace_id, None)
    
    @property
    def is_connected(self) -> bool:
        """检查 WebSocket 是否已连接"""
        if self.websocket is None:
            return False
        try:
            return not self.websocket.closed
        except AttributeError:
            return self.websocket is not None
    
    def register_audio_file(self, task_id: str, file_path: Path) -> None:
        """
        注册音频文件
        
        Args:
            task_id: 任务ID
            file_path: 音频文件路径
        """
        self.audio_files[task_id] = file_path
        logger.debug(f"注册音频文件: task_id={task_id}, path={file_path}")
    
    def pop_audio_file(self, task_id: str) -> Optional[Path]:
        """
        获取并移除音频文件路径
        
        Args:
            task_id: 任务ID
            
        Returns:
            音频文件路径，如果不存在则返回 None
        """
        file_path = self.audio_files.pop(task_id, None)
        if file_path:
            logger.debug(f"获取音频文件: task_id={task_id}, path={file_path}")
        return file_path

    def set_output_text(self, text: str) -> None:
        """
        设置最近一次输出文本
        
        Args:
            text: 输出文本内容
        """
        self.last_output_text = text

