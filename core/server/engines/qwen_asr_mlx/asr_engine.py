# coding=utf-8
"""
Qwen3-ASR MLX 适配器

设计目标：
1. 复用 CapsWriter 现有 BaseASREngine 抽象，不改动上层 TaskPipeline。
2. 首版优先跑通“松开后快速返回最终结果”的闭环，不强行接入中间流式显示。
3. 与现有 Windows 的 qwen_asr_gguf 并存，严格把平台差异收敛在引擎层。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

import numpy as np

from ..base import BaseASREngine, RecognitionStream, EngineCapabilities
from ..language import get_language, ENGINE_QWEN_ASR

QWEN3_ASR_SAMPLE_RATE = 16000


@dataclass
class ASREngineConfig:
    """
    Qwen3-ASR MLX 运行配置

    Attributes:
        model: 本地模型目录或 Hugging Face 仓库 ID。
        return_timestamps: 是否向上游请求词级时间戳。
        max_new_tokens: 可选的生成 token 上限；None 表示让上游库按音频长度自动推导。
        verbose: 是否打印上游库的详细推理日志。
    """

    model: str
    return_timestamps: bool = False
    max_new_tokens: Optional[int] = None
    verbose: bool = False


class QwenASRMLXStream(RecognitionStream):
    """
    Qwen3-ASR MLX 识别流

    当前实现仍是“整段音频一次性送入 Session.transcribe”的最终结果模式，
    但保留标准 RecognitionStream 形态，确保后续若要接中间流式状态时不必重写上层接口。
    """

    def __init__(self, sample_rate: int = 16000):
        super().__init__(sample_rate)
        self.audio_data: Optional[np.ndarray] = None

    def accept_waveform(self, sample_rate: int, audio: np.ndarray):
        """
        接收一段音频。

        这里统一转成 float32 numpy，避免把上游库依赖泄露到 TaskPipeline。
        采样率是否需要重采样放到 decode 阶段统一处理，这样可以把“输入标准化”和
        “模型目标采样率适配”两件事分开，后续排查也更直观。
        """
        self.sample_rate = sample_rate
        self.audio_data = np.asarray(audio, dtype=np.float32)


class QwenASRMLXEngine(BaseASREngine):
    """
    Qwen3-ASR MLX 推理引擎适配器

    通过 `mlx_qwen3_asr.Session` 持有模型与 tokenizer，复用其同步 `transcribe` API。
    """

    def __init__(self, config: ASREngineConfig):
        super().__init__(config)

        try:
            # 延迟导入第三方依赖，避免非 macOS / 非 MLX 路线在模块导入阶段就失败。
            from mlx_qwen3_asr import Session
        except ImportError as exc:
            raise RuntimeError(
                "未安装 mlx-qwen3-asr。请在 macOS 环境执行 `pip install -r requirements-server.txt`。"
            ) from exc

        try:
            # Session 会在初始化阶段加载模型并持有 tokenizer，适合当前服务端常驻进程模型生命周期。
            self.session = Session(model=self.config.model)
        except Exception as exc:
            raise RuntimeError(
                f"Qwen3-ASR MLX 模型加载失败: {self.config.model}"
            ) from exc

    @property
    def capabilities(self) -> List[EngineCapabilities]:
        """
        声明引擎能力。

        当前只把首版已经实际接入并验证链路的能力暴露给上层：
        - ASR：基础语音识别
        - PUNC：模型自带标点输出

        这里暂时不声明 TIMESTAMPS，原因是首版目标是尽快跑通最终结果；
        时间戳能力后续单独验收，再决定是否切换到原生段对齐结果。
        """
        return [
            EngineCapabilities.ASR,
            EngineCapabilities.PUNC,
        ]

    def create_stream(self, hotwords: Optional[str] = None) -> QwenASRMLXStream:
        """
        创建识别流。

        MLX 路线当前不支持动态热词透传，因此 hotwords 参数先保留接口但不使用。
        """
        return QwenASRMLXStream()

    def decode_stream(
        self,
        stream: QwenASRMLXStream,
        context: Optional[str] = None,
        language: Optional[str] = None,
        **kwargs: Any,
    ):
        """
        执行一次最终结果识别。

        参数策略：
        - `context` 直接透传给上游，作为领域上下文提示。
        - `language` 复用现有 Qwen 语言映射，保持前后端统一语言配置口径。
        - `return_timestamps` 默认走配置项，后续如需临时覆盖可以通过 kwargs 传入。
        """
        if stream.audio_data is None or stream.audio_data.size == 0:
            return

        mapped_lang = get_language(ENGINE_QWEN_ASR, language) if language else None
        return_timestamps = bool(
            kwargs.get('return_timestamps', self.config.return_timestamps)
        )
        max_new_tokens = kwargs.get('max_new_tokens', self.config.max_new_tokens)
        verbose = bool(kwargs.get('verbose', self.config.verbose))
        prepared_audio, prepared_sample_rate = self._prepare_audio_for_session(
            stream.audio_data,
            stream.sample_rate,
        )

        transcription = self.session.transcribe(
            # 这里始终把音频整理成 16kHz 后再透传给上游 Session。
            # 设计意图：
            # 1. Qwen3-ASR 的目标采样率就是 16kHz，本地先重采样不会改变主链路语义。
            # 2. `mlx-qwen3-asr` 遇到非 16kHz 音频时会尝试调用 ffmpeg 重采样；
            #    当前项目并未把 ffmpeg 设为服务端硬依赖，因此这里要主动兜底。
            # 3. 这样可以把“环境缺少 ffmpeg”从运行时阻断，降级为引擎内部的透明处理。
            (prepared_audio, prepared_sample_rate),
            context=context or "",
            language=mapped_lang,
            return_timestamps=return_timestamps,
            max_new_tokens=max_new_tokens,
            verbose=verbose,
        )

        stream.result.text = (transcription.text or "").strip()
        stream.result.language = getattr(transcription, 'language', None)
        stream.result.performance = {
            'finish_reason': getattr(transcription, 'finish_reason', None),
            'truncated': getattr(transcription, 'truncated', False),
        }

        # 当前上层主链路并不依赖这些字段，但当上游返回了 segments 时顺手填充，
        # 便于后续文件转录阶段逐步接回原生时间戳而不必重写适配层。
        segments = getattr(transcription, 'segments', None) or []
        if segments:
            stream.result.tokens = self._segments_to_tokens(segments)
            stream.result.timestamps = self._segments_to_timestamps(segments)

    def update_hotwords(self, hotwords: List[str]):
        """
        MLX Session 当前没有与 CapsWriter 热词系统等价的动态注入口。

        首版按已收敛范围保持 no-op，把热词增强继续留在客户端后处理链路。
        """
        return None

    def cleanup(self):
        """
        释放资源。

        `mlx_qwen3_asr.Session` 暂无显式 close 接口，因此这里采用删除持有引用 +
        尝试清理 MLX cache 的保守策略，避免服务端长期运行时积累不必要缓存。
        """
        self.session = None
        self._clear_mlx_cache_safely()

    @staticmethod
    def _segments_to_tokens(segments: List[dict]) -> List[str]:
        """
        将上游 segments 转成简单 token 序列。

        上游返回的 `segments` 目前是 `{text, start, end}` 结构，粒度可能是词、字或短片段。
        这里保持“一段文本对应一个 token”的最小转换，后续若需要更细粒度再单独演进。
        """
        tokens: List[str] = []
        for item in segments:
            text = str(item.get('text', '')).strip()
            if text:
                tokens.append(text)
        return tokens

    @staticmethod
    def _segments_to_timestamps(segments: List[dict]) -> List[float]:
        """
        将上游 segments 起始时间抽取为时间戳列表。

        TaskPipeline 的 token 合并逻辑只要求 token/timestamp 对齐即可，
        因此首版使用 segment 起始时间作为每个 token 的代表时间。
        """
        timestamps: List[float] = []
        for item in segments:
            text = str(item.get('text', '')).strip()
            if not text:
                continue
            timestamps.append(float(item.get('start', 0.0) or 0.0))
        return timestamps

    @staticmethod
    def _clear_mlx_cache_safely():
        """
        尽量释放 MLX 缓存，但不把清缓存失败上抛成业务错误。

        这样做的原因是：
        - MLX 各版本清缓存 API 名称有差异；
        - cleanup 通常发生在服务退出或模型切换时，不应因为清缓存细节失败影响主流程。
        """
        try:
            import mlx.core as mx
        except Exception:
            return

        clear_cache = getattr(mx, 'clear_cache', None)
        if callable(clear_cache):
            clear_cache()
            return

        metal = getattr(mx, 'metal', None)
        metal_clear_cache = getattr(metal, 'clear_cache', None)
        if callable(metal_clear_cache):
            metal_clear_cache()

    @staticmethod
    def _prepare_audio_for_session(
        audio: np.ndarray,
        sample_rate: int,
    ) -> tuple[np.ndarray, int]:
        """
        将输入音频整理为上游 Session 最稳妥的 16kHz float32 形态。

        这里显式在本地完成重采样，而不是把责任交给上游库去调用 ffmpeg，
        目的是降低环境耦合，让服务端在未安装 ffmpeg 的 macOS 本机也能稳定工作。
        """
        normalized_audio = np.asarray(audio, dtype=np.float32)
        if sample_rate == QWEN3_ASR_SAMPLE_RATE:
            return normalized_audio, sample_rate
        return (
            QwenASRMLXEngine._resample_audio_linear(
                normalized_audio,
                sample_rate,
                QWEN3_ASR_SAMPLE_RATE,
            ),
            QWEN3_ASR_SAMPLE_RATE,
        )

    @staticmethod
    def _resample_audio_linear(
        audio: np.ndarray,
        source_sample_rate: int,
        target_sample_rate: int,
    ) -> np.ndarray:
        """
        使用线性插值做最小可用重采样。

        这里不追求做成高保真音频处理器，只要求满足语音识别前置标准化：
        - 算法简单、无额外依赖；
        - 对短语音指令足够稳定；
        - 能把“缺少 ffmpeg”从致命错误降为内部实现细节。
        """
        if audio.size == 0:
            return audio.astype(np.float32, copy=False)
        if source_sample_rate <= 0 or target_sample_rate <= 0:
            raise ValueError(
                f"非法采样率: source={source_sample_rate}, target={target_sample_rate}"
            )

        target_size = int(round(audio.size * target_sample_rate / source_sample_rate))
        if target_size <= 0:
            return np.asarray([], dtype=np.float32)

        source_positions = np.linspace(0.0, 1.0, num=audio.size, endpoint=False)
        target_positions = np.linspace(0.0, 1.0, num=target_size, endpoint=False)
        return np.interp(target_positions, source_positions, audio).astype(np.float32)
