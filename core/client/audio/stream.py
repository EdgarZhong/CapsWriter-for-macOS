# coding: utf-8
"""
音频流管理模块

提供 AudioStreamManager 类用于管理音频输入流，包括流的创建、
启动、停止和设备检测。
"""

from __future__ import annotations

import time
import threading
import platform
from typing import TYPE_CHECKING, Optional

import numpy as np
import sounddevice as sd

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
    CLOSE_TIMEOUT = 1.0  # 原生关闭失联时限；不能让按键分发线程等待旧版的整整 5 秒。
    CALLBACK_STOP_TIMEOUT = 0.12  # 先给回调自然退出的机会，再走原生 abort 兜底。
    
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
        # 即使业务已结束，也保留正在关闭/关闭失败的资源所有权，禁止重载仍在使用的库。
        self._close_thread: Optional[threading.Thread] = None
        self._closing_stream = None
        self._close_error = False
        self._close_phase = 'idle'
        self._stop_requested = threading.Event()
        self._stream_finished = threading.Event()

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
        # 回调主动退出可以先终止 CoreAudio 采集，再由普通线程释放句柄；不能在
        # finished_callback 中反过来关闭/重开自己，否则会与原生回调栈交叉等待。
        if self._stop_requested.is_set():
            raise sd.CallbackAbort
        # 只在录音状态时处理数据
        if not self.state.recording:
            return
        
        # 实时线程只复制数据并投递；日志、numpy 统计和 trace 更新可能抢锁/写盘，
        # 移到 asyncio 线程，避免松手时原生关闭等待一个被日志阻塞的音频回调。
        if self.app.loop and self.state.queue_in:
            enqueue_time = time.time()
            trace_id = self.state.active_trace_id
            queue_in = self.state.queue_in
            audio_data = indata.copy()
            self.app.loop.call_soon_threadsafe(
                self._enqueue_audio, audio_data, frames, enqueue_time, trace_id, status, queue_in,
            )

    def _enqueue_audio(self, audio_data, frames, enqueue_time, trace_id, status, queue_in) -> None:
        """在事件循环中交付已采集的音频；只对前五块统计能量，保留停止前在途数据。"""
        if status:
            logger.warning("[audio] callback status: %s", status)
        self.state.mark_first_audio_enqueue(trace_id, enqueue_time, frames)
        context = self.state.trace_contexts.get(trace_id, {})
        if context.get('audio_metric_count', 0) < 5:

            # 记录前几帧音频的能量特征，用于判断当前录音链路里拿到的到底是
            # 真实麦克风波形、近零静音帧，还是异常的全零数据。
            mean_abs = float(np.mean(np.abs(audio_data)))
            rms = float(np.sqrt(np.mean(np.square(audio_data))))
            peak = float(np.max(np.abs(audio_data)))
            zero_ratio = float(np.mean(audio_data == 0.0))
            channels = int(audio_data.shape[1]) if audio_data.ndim > 1 else 1

            self.state.mark_audio_metrics(
                trace_id=trace_id,
                rms=rms,
                peak=peak,
                mean_abs=mean_abs,
                zero_ratio=zero_ratio,
                channels=channels,
            )
        # 持有采集时的队列，而非执行时全局队列，防止旧流迟到的数据污染下一条。
        queue_in.put_nowait({
            'type': 'data', 'time': enqueue_time, 'data': audio_data, 'trace_id': trace_id,
        })

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
            if self.state.stream is None:
                logger.info("[audio] stream open requested by recording session")
                if self.start() is None:
                    return False
            # 在启动成功之后计数，避免失败回收 stop() 清零后重试成功却没有会话。
            self._recording_session_count += 1
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
        """原生回调只发信号，绝不从回调栈重入 stop/start/PortAudio 重载。"""
        self._stream_finished.set()
        if not self._stop_requested.is_set() and self.app.loop:
            self.app.loop.call_soon_threadsafe(self._schedule_recovery, self._stream_finished)

    def _schedule_recovery(self, finished) -> None:
        """把意外结束的恢复放到普通线程，并用本轮事件身份丢弃迟到的旧流通知。"""
        def recover():
            with self._session_lock:
                if (finished is not self._stream_finished or not self._running
                        or self._stop_requested.is_set()):
                    return
                logger.warning("[audio] 音频流意外结束，尝试在回调之外恢复")
                self.reopen()
        threading.Thread(target=recover, daemon=True, name='AudioStreamRecovery').start()
    
    def _reload_portaudio(self) -> None:
        """
        在所有流均已释放时重新初始化设备列表，不卸载动态库。

        PortAudio 在首次 `import sounddevice` 时会把整张设备列表和默认设备
        索引一次性缓存（`Pa_Initialize`），运行期不会自动刷新。当系统音频拓扑
        发生变化时——例如：
        - 接入/拔出耳机、AirPods、USB 麦克风（默认输入设备被 macOS 切换）；
        - 启动 SoundSource 等使用虚拟音频驱动（ACE/ARK）的软件（设备增删）；
        旧缓存里的设备句柄/默认索引会失效，导致 `device=None` 指向错误或
        失效的设备而录不到音。本方法走 terminate → initialize
        的流程重建缓存，使后续 `query_devices` / `InputStream(device=None)`
        都基于当前真实的设备拓扑与默认输入设备。

        仅开流失败后的单次恢复使用。关闭未完成时绝不能 terminate，更不能 dlclose
        仍有回调或关闭线程在执行的库；state.stream=None 不代表资源已释放。
        """
        if self.state.stream is not None or self._closing_stream is not None:
            raise RuntimeError('音频流尚未释放，禁止刷新 PortAudio')
        try:
            sd._terminate()
            sd._initialize()
        except Exception as e:
            logger.warning(f"重载 PortAudio 时发生警告: {e}")

    def _find_builtin_mic(self) -> Optional[int]:
        """
        在设备列表中查找 Mac 内建麦克风，返回其设备索引。

        临时策略（2026-08-12）：默认输入设备会跟随耳机 / AirPods 等外设自动切换，
        而耳机麦克风收音效果差，用户希望固定使用本机内建麦克风录音。这里按设备名
        匹配内建麦克风：
        - 中文系统：`MacBook Air麦克风`、`MacBook Pro麦克风`、`内建麦克风`
        - 英文系统：`MacBook Air Microphone`、`Built-in Microphone`
        找不到（例如 Mac mini 外接声卡）时返回 None，由调用方回退到默认输入设备。
        """
        try:
            for index, dev in enumerate(sd.query_devices()):
                if dev['max_input_channels'] <= 0:
                    continue
                name = dev.get('name', '')
                if ('内建' in name or 'Built-in' in name
                        or ('麦克风' in name and 'MacBook' in name)
                        or ('Microphone' in name and 'MacBook' in name)):
                    logger.info(f"找到内建麦克风: {name} (index={index})")
                    return index
        except Exception as e:
            logger.warning(f"查找内建麦克风失败: {e}")
        return None

    def start(self) -> Optional[sd.InputStream]:
        """串行创建/启动流；正常路径不刷新设备，失败时仅安全重试一次。"""
        with self._session_lock:
            if self._closing_stream is not None or self._close_error:
                logger.error("[audio] 旧流尚未可靠释放，拒绝重新打开麦克风；请重启客户端")
                return None
            if self._close_thread is not None and self._close_thread.is_alive():
                return None
            if self._running:
                return self.state.stream

            started = time.monotonic()
            is_macos = platform.system() == 'Darwin'
            # 20ms 分块减少首帧等待；其它平台保留原有 50ms 行为。使用 low 输入
            # 延迟，而非 sounddevice 面向稳定播放的默认 high 延迟。
            block_duration = 0.02 if is_macos else self.BLOCK_DURATION
            for attempt in range(2 if is_macos else 1):
                stream = None
                try:
                    if attempt:
                        self._reload_portaudio()
                    device_index = self._find_builtin_mic() if is_macos else None
                    if is_macos and device_index is None and not attempt:
                        # 无内建麦克风时仍须跟随系统默认输入；旧默认设备即使已不再是
                        # 默认，也可能继续成功打开，因此这条回退路径必须先刷新设备表。
                        self._reload_portaudio()
                        device_index = self._find_builtin_mic()
                    device = (sd.query_devices(device_index) if device_index is not None
                              else sd.query_devices(kind='input'))
                    self._channels = min(2, device['max_input_channels'])
                    if self._channels < 1:
                        raise RuntimeError('输入设备没有可用录音声道')
                    device_name = device.get('name', '未知设备')
                    logger.info("找到音频设备: %s, 声道数: %s", device_name, self._channels)
                    device_ready = time.monotonic()
                    self._stop_requested = threading.Event()
                    self._stream_finished = threading.Event()
                    options = {'latency': 'low'} if is_macos else {}
                    stream = sd.InputStream(
                        samplerate=self.SAMPLE_RATE,
                        blocksize=int(block_duration * self.SAMPLE_RATE),
                        device=device_index, dtype="float32", channels=self._channels,
                        callback=self._audio_callback,
                        finished_callback=self._on_stream_finished, **options,
                    )
                    constructed = time.monotonic()
                    stream.start()
                    self.state.stream = stream
                    self._running = True
                    # 延迟关闭成功后下一次开流可恢复，撤销此前“麦克风不可用”标记；
                    # recording/ready 的整体状态仍由正式录音任务负责发布。
                    eb = getattr(self.app, 'error_bus', None)
                    if eb is not None:
                        eb.update(microphone_ok=True)
                    logger.info(
                        "[audio] stream open device_ms=%.1f construct_ms=%.1f start_ms=%.1f total_ms=%.1f",
                        (device_ready - started) * 1000, (constructed - device_ready) * 1000,
                        (time.monotonic() - constructed) * 1000,
                        (time.monotonic() - started) * 1000,
                    )
                    return stream
                except Exception:
                    logger.error("[audio] 创建/启动音频流失败 attempt=%s", attempt + 1, exc_info=True)
                    if stream is not None:
                        # 构造成功但 start 失败仍占有资源，复用带超时的关闭路径；
                        # 关闭失败就禁止刷新/重试，避免第二次故障掩盖第一个泄漏。
                        self.state.stream = stream
                        self._running = True
                        self.stop()
                        if self._closing_stream is not None or self._close_error:
                            return None
            return None

    def stop(self) -> None:
        """请求回调退出并有界等待释放；只有 close 确认成功才能宣告关闭。"""
        with self._session_lock:
            if not self._running:
                return
            self._running = False
            self._recording_session_count = 0
            stream = self.state.stream
            self.state.stream = None
            if stream is None:
                return
            self._closing_stream = stream
            self._stop_requested.set()
            self._close_error = False
            self._close_phase = 'callback_exit'
            finished = self._stream_finished
            began = time.monotonic()

            def close_stream():
                """所有原生控制调用都在普通线程执行，错误码必须检查而非静默忽略。"""
                try:
                    finished.wait(self.CALLBACK_STOP_TIMEOUT)
                    self._close_phase = 'abort'
                    abort_started = time.monotonic()
                    try:
                        # 回调已经退出时 abort 仍将 PortAudio 状态归一为 stopped；
                        # start 失败的流也可能未 active，不依赖 active 查询作为前置门。
                        stream.abort(ignore_errors=False)
                    except Exception:
                        logger.warning("[audio] abort 返回错误，继续尝试 close", exc_info=True)
                    logger.info("[audio] abort returned elapsed_ms=%.1f",
                                (time.monotonic() - abort_started) * 1000)
                    self._close_phase = 'close'
                    stream.close(ignore_errors=False)
                except Exception:
                    self._close_error = True
                    self._close_phase = 'failed'
                    logger.error("[audio] 音频流关闭失败，保留资源故障状态", exc_info=True)
                    self._notify_stream_leak()
                else:
                    # 只有此处代表原生资源已释放；超时不销毁引用、不卸载库。
                    self._closing_stream = None
                    self._close_phase = 'closed'
                    logger.info("[audio] stream close total_ms=%.1f",
                                (time.monotonic() - began) * 1000)

            self._close_thread = threading.Thread(
                target=close_stream, daemon=True, name='AudioStreamClose',
            )
            self._close_thread.start()
            self._close_thread.join(timeout=self.CLOSE_TIMEOUT)
            if self._close_thread.is_alive() and self._close_phase not in ('failed', 'closed'):
                logger.error("[audio] 关闭超时 phase=%s timeout_s=%.1f；禁止重开及重载底层库",
                             self._close_phase, self.CLOSE_TIMEOUT)
                self._notify_stream_leak()

    def _notify_stream_leak(self) -> None:
        """无法确认释放时通知用户；故障态不得承诺继续录音安全可用。"""
        try:
            eb = getattr(self.app, 'error_bus', None)
            if eb is not None:
                # 新录音已被资源守卫暂停，菜单栏不能继续显示绿色“运行正常”。
                eb.update(state='error', microphone_ok=False)
                eb.notify(
                    "麦克风关闭未完成，已暂停新录音以避免重复占用；"
                    "请从菜单栏重启 CapsWriter。若后台关闭稍后成功，可恢复录音。",
                    'stream_leak',
                )
        except Exception as e:
            logger.debug("[audio] 发送音频流故障通知失败: %s", e)

    def reopen(self) -> Optional[sd.InputStream]:
        """在普通线程串行重开，保留会话计数；不双重重载、不增加固定 sleep。"""
        with self._session_lock:
            count = self._recording_session_count
            self.stop()
            stream = self.start()
            if stream is not None:
                self._recording_session_count = count
            return stream
