# coding: utf-8
"""
音频流管理模块

提供 AudioStreamManager 类用于管理音频输入流，包括流的创建、
启动、停止和设备检测。
"""

from __future__ import annotations

import sys
import time
import threading
import platform
from typing import TYPE_CHECKING, Optional

import numpy as np
import sounddevice as sd

from core.client.state import console
from . import logger

if TYPE_CHECKING:
    from core.client.state import ClientState
    from ..app import CapsWriterClient



class AudioStreamManager:
    """
    音频流管理器
    
    负责管理音频输入流的生命周期，包括：
    - 检测和选择音频设备
    - 创建和启动音频流
    - 处理音频数据回调
    - 流的重启和关闭
    
    Attributes:
        state: 客户端状态实例
        sample_rate: 采样率（默认 48000Hz）
        block_duration: 每个数据块的时长（秒，默认 0.05s）
    """
    
    SAMPLE_RATE = 48000
    BLOCK_DURATION = 0.05  # 50ms
    
    def __init__(self, app: CapsWriterClient):
        """
        初始化音频流管理器
        
        Args:
            app: 客户端 App 实例
        """
        self.app = app
        self._channels = 1
        self._running = False  # 标志是否应该运行
        self._recording_session_count = 0
        self._session_lock = threading.RLock()

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state
    
    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info,
        status: sd.CallbackFlags
    ) -> None:
        """
        音频数据回调函数
        
        当音频流接收到新数据时调用，将数据放入异步队列中。
        """
        # 只在录音状态时处理数据
        if not self.state.recording:
            return
        
        import asyncio
        
        # 将数据放入队列
        if self.app.loop and self.state.queue_in:
            enqueue_time = time.time()
            trace_id = self.state.active_trace_id
            audio_data = indata.copy()

            # 记录前几帧音频的能量特征，用于判断当前录音链路里拿到的到底是
            # 真实麦克风波形、近零静音帧，还是异常的全零数据。
            mean_abs = float(np.mean(np.abs(audio_data)))
            rms = float(np.sqrt(np.mean(np.square(audio_data))))
            peak = float(np.max(np.abs(audio_data)))
            zero_ratio = float(np.mean(audio_data == 0.0))
            channels = int(audio_data.shape[1]) if audio_data.ndim > 1 else 1

            # 这里只记录“第一帧真正进入队列”的时刻。
            # 如果后续发现录音任务并不是由按键按下直接驱动，这个点会和按键时间线明显错位。
            self.state.mark_first_audio_enqueue(
                trace_id=trace_id,
                enqueue_time=enqueue_time,
                frames=frames,
            )
            self.state.mark_audio_metrics(
                trace_id=trace_id,
                rms=rms,
                peak=peak,
                mean_abs=mean_abs,
                zero_ratio=zero_ratio,
                channels=channels,
            )
            asyncio.run_coroutine_threadsafe(
                self.state.queue_in.put({
                    'type': 'data',
                    'time': enqueue_time,
                    'data': audio_data,
                    'trace_id': trace_id,
                }),
                self.app.loop
            )

    def should_start_immediately(self) -> bool:
        """
        判断当前平台是否需要在客户端启动时立即打开输入流。

        macOS 的新 Caps Lock 方案要求：
        - 客户端空闲时不要长期占用麦克风；
        - 只有长按真正进入录音时，系统左侧麦克风指示才应该出现。
        因此在 Darwin + `remap_f18` 模式下默认走按需开流。
        """
        from config_client import ClientConfig as Config

        if platform.system() != 'Darwin':
            return True

        if getattr(Config, 'macos_caps_mode', 'off') != 'remap_f18':
            return True

        return not getattr(Config, 'macos_caps_open_stream_on_demand', True)

    def start_recording_session(self) -> bool:
        """
        声明一次新的录音会话即将开始。

        返回值语义：
        - `True`：当前录音会话具备可用音频流；
        - `False`：音频流启动失败，本次录音不应继续推进。
        """
        with self._session_lock:
            self._recording_session_count += 1

            if self.should_start_immediately():
                success = self.state.stream is not None or self.start() is not None
                if not success and self._recording_session_count > 0:
                    self._recording_session_count -= 1
                return success

            if self.state.stream is None:
                logger.info("[audio] stream open requested by recording session")
                success = self.start() is not None
                if not success and self._recording_session_count > 0:
                    self._recording_session_count -= 1
                return success

            return True

    def stop_recording_session(self) -> None:
        """
        声明一次录音会话已经结束。

        在 macOS 按需开流模式下，最后一个录音会话结束时立即关闭输入流，
        让系统麦克风占用指示同步消失。
        """
        with self._session_lock:
            if self._recording_session_count > 0:
                self._recording_session_count -= 1

            if self.should_start_immediately():
                return

            if self._recording_session_count == 0 and self.state.stream is not None:
                logger.info("[audio] stream close requested by recording session end")
                self.stop()
    
    def _on_stream_finished(self) -> None:
        """音频流结束回调"""
        if not threading.main_thread().is_alive():
            return
        if not self._running:
            return
        
        logger.info("音频流意外结束，正在尝试重启...")
        self.reopen()
    
    def start(self) -> Optional[sd.InputStream]:
        """
        启动音频流
        
        Returns:
            创建的音频输入流，如果失败返回 None
        """
        if self._running:
            logger.debug("音频流已在运行，跳过启动")
            return self.state.stream
            
        # 检测音频设备
        try:
            device = sd.query_devices(kind='input')
            self._channels = min(2, device['max_input_channels'])
            device_name = device.get('name', '未知设备')
            console.print(
                f'使用默认音频设备：[italic]{device_name}，声道数：{self._channels}',
                end='\n\n'
            )
            logger.info(f"找到音频设备: {device_name}, 声道数: {self._channels}")
        except UnicodeDecodeError:
            logger.warning("无法获取音频设备名称（编码问题）")
        except sd.PortAudioError:
            logger.error("未找到麦克风设备")
            input('按回车键退出')
            sys.exit(1)
        
        # 创建音频流
        try:
            stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                blocksize=int(self.BLOCK_DURATION * self.SAMPLE_RATE),
                device=None,
                dtype="float32",
                channels=self._channels,
                callback=self._audio_callback,
                finished_callback=self._on_stream_finished,
            )
            stream.start()
            
            self.state.stream = stream
            self._running = True
            logger.info("[audio] stream open")
            logger.debug(
                f"音频流已启动: 采样率={self.SAMPLE_RATE}, "
                f"块大小={int(self.BLOCK_DURATION * self.SAMPLE_RATE)}"
            )
            return stream
            
        except Exception as e:
            logger.error(f"创建音频流失败: {e}", exc_info=True)
            return None
    
    def stop(self) -> None:
        """停止音频流"""
        if not self._running:
            return

        self._running = False  # 标记为停止
        self._recording_session_count = 0
        stream = self.state.stream
        self.state.stream = None  # 立即清除引用，允许新录音会话判断流已不可用
        if stream is not None:
            # sounddevice.InputStream.close() 在录音时间极短时可能在 macOS 上卡死（PortAudio 已知问题）。
            # 用带 5s 超时的后台线程执行，超时后放弃等待，避免持有 _session_lock 导致后续按键全部无响应。
            def _close():
                try:
                    stream.close()
                except Exception as e:
                    logger.debug(f"停止音频流时发生错误: {e}")

            t = threading.Thread(target=_close, daemon=True)
            t.start()
            t.join(timeout=5.0)
            if t.is_alive():
                logger.warning("[audio] stream.close() 超时（5s），已放弃等待，PortAudio 流将在后台自行结束")
            else:
                logger.info("[audio] stream close")
                logger.debug("音频流已停止")
    
    def reopen(self) -> Optional[sd.InputStream]:
        """
        重新启动音频流
        
        Returns:
            新创建的音频输入流
        """
        logger.info("正在重启音频流...")
        
        # 停止旧流
        self.stop()
        
        # 重载 PortAudio，更新设备列表
        try:
            sd._terminate()
            sd._ffi.dlclose(sd._lib)
            sd._lib = sd._ffi.dlopen(sd._libname)
            sd._initialize()
        except Exception as e:
            logger.warning(f"重载 PortAudio 时发生警告: {e}")
        
        # 等待设备稳定
        time.sleep(0.1)
        
        # 启动新流
        return self.start()
