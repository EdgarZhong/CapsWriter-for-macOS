# coding=utf-8
"""
Qwen3-ASR MLX 适配器

设计目标：
1. 复用 CapsWriter 现有 BaseASREngine 抽象，继续与其它后端并存。
2. qwen_asr_mlx 主路径改为进入 package-owned Runner，由 Runner 管理完整 task_id 生命周期。
3. 推理级参数集中到 mlx-qwen3-asr package 内，server 外层只保留模型入口和请求元信息。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional
import sys

import numpy as np

from ..base import BaseASREngine, RecognitionStream, EngineCapabilities
from .. import logger
from ..language import get_language, ENGINE_QWEN_ASR

QWEN3_ASR_SAMPLE_RATE = 16000


@dataclass
class ASREngineConfig:
    """
    Qwen3-ASR MLX 运行配置

    Attributes:
        model: 本地模型目录或 Hugging Face 仓库 ID。
        enable_startup_prewarm: 是否在 server 启动时做一次真实推理预热。
        enable_wired_memory: 是否要求Runner锁住全部权重页；失败时拒绝启动。
        wired_memory_limit: 权重锁页预算上限，'auto' 由package根据active memory估算。
    """

    model: str
    enable_startup_prewarm: bool = True
    enable_wired_memory: bool = True
    wired_memory_limit: str | int | None = "auto"


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

        这里统一转成 float32 numpy，避免把上游库依赖泄露到 WorkPipeline。
        采样率是否需要重采样放到 decode 阶段统一处理，这样可以把“输入标准化”和
        “模型目标采样率适配”两件事分开，后续排查也更直观。
        """
        self.sample_rate = sample_rate
        self.audio_data = np.asarray(audio, dtype=np.float32)


class QwenASRMLXEngine(BaseASREngine):
    """
    Qwen3-ASR MLX 推理引擎适配器

    通过 `mlx_qwen3_asr.QwenASRRunner` 持有完整任务生命周期，避免 server 外层继续
    维护 Qwen3-ASR 的语义分段、generation 参数和结果拼接策略。
    """

    uses_task_runner = True

    def __init__(self, config: ASREngineConfig):
        super().__init__(config)
        self._ensure_local_package_precedence()

        try:
            # 延迟导入第三方依赖，避免非 macOS / 非 MLX 路线在模块导入阶段就失败。
            import mlx_qwen3_asr
            from mlx_qwen3_asr import CapsWriterRunnerConfig, QwenASRRunner
        except ImportError as exc:
            raise RuntimeError(
                "未安装 mlx-qwen3-asr。请在 macOS 环境执行 `pip install -r requirements-server.txt`。"
            ) from exc

        try:
            # Runner 会在 package 内部创建 Session 并集中持有推理参数；server 不再传 max_new_tokens 等配置。
            runner_config = CapsWriterRunnerConfig(
                enable_startup_prewarm=bool(self.config.enable_startup_prewarm),
                enable_wired_memory=bool(self.config.enable_wired_memory),
                wired_memory_limit=self.config.wired_memory_limit,
            )
            self.runner = QwenASRRunner(model=self.config.model, config=runner_config)
            self.package_file = getattr(mlx_qwen3_asr, "__file__", "")
            self._log_runner_runtime_info()
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
        - generation、chunking、timestamps 等推理级参数由 package Runner 默认配置集中管理。
        """
        if stream.audio_data is None or stream.audio_data.size == 0:
            return

        mapped_lang = get_language(ENGINE_QWEN_ASR, language) if language else None
        transcription = self.runner.transcribe_audio(
            stream.audio_data,
            task_id=f"legacy-stream-{id(stream)}",
            sample_rate=stream.sample_rate,
            context=context or "",
            language=mapped_lang,
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

    def feed_audio_patch(
        self,
        *,
        task_id: str,
        audio: np.ndarray,
        sample_rate: int,
        is_final: bool,
        context: Optional[str] = None,
        language: Optional[str] = None,
        source: str = "",
    ):
        """
        Runner 主路径：按同一个 task_id 持续喂入音频增量。

        返回值为 None 表示该 patch 只完成缓冲；当 final patch 到达时，Runner 返回完整结果。
        """
        from mlx_qwen3_asr import AudioFeedPatch

        mapped_lang = get_language(ENGINE_QWEN_ASR, language) if language else None
        return self.runner.feed_audio(
            AudioFeedPatch(
                task_id=task_id,
                audio=audio,
                sample_rate=sample_rate,
                is_final=is_final,
                context=context or "",
                language=mapped_lang,
                source=source,
            )
        )

    def cancel_task(self, task_id: str) -> None:
        """释放 Runner 内某个 task_id 的音频缓冲，用于客户端断连清理。"""
        if hasattr(self, "runner"):
            self.runner.cancel_task(task_id)

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
        if getattr(self, "runner", None) is not None:
            self.runner.cleanup()
        self.runner = None
        self._clear_mlx_cache_safely()

    def _log_runner_runtime_info(self) -> None:
        """把 package Runner 的启动预热和 wired memory 状态写入 server 日志。"""
        info = self.runner.runtime_info()
        logger.info(f"Qwen3-ASR MLX package path: {self.package_file}")

        prewarm = info.get("prewarm_info", {}) or {}
        if prewarm.get("enabled") is False:
            logger.info("Qwen Runner startup prewarm disabled")
        elif prewarm.get("ok"):
            logger.info(
                "Qwen Runner startup prewarm completed: "
                f"cost={float(prewarm.get('cost_sec', 0.0)):.3f}s, "
                f"audio={float(prewarm.get('seconds', 0.0)):.2f}s, "
                f"finish_reason={prewarm.get('finish_reason')}, "
                f"truncated={prewarm.get('truncated')}"
            )
        else:
            logger.warning(
                "Qwen Runner startup prewarm failed: "
                f"cost={float(prewarm.get('cost_sec', 0.0)):.3f}s, "
                f"error={prewarm.get('error')}"
            )

        wired = info.get("wired_memory_info", {}) or {}
        if wired.get("enabled") is False:
            logger.info("Qwen Runner wired memory disabled")
        elif wired.get("ok"):
            logger.info(
                "Qwen Runner weights locked: "
                f"method={wired.get('method')}, "
                f"locked={self._format_bytes(int(wired.get('locked_bytes', 0)))}, "
                f"active={self._format_bytes(int(wired.get('active_bytes', 0)))}, "
                f"limit={self._format_bytes(int(wired.get('limit_bytes', 0)))}, "
                f"recommended={self._format_bytes(int(wired.get('recommended_bytes', 0)))}"
            )
        else:
            logger.warning(
                "Qwen Runner wired memory unavailable: "
                f"reason={wired.get('reason') or wired.get('error')}"
            )

    @staticmethod
    def _format_bytes(value: int) -> str:
        """把字节数格式化为 GiB/MiB，便于阅读 server 日志。"""
        if value <= 0:
            return "0B"
        gib = 1024 ** 3
        mib = 1024 ** 2
        if value >= gib:
            return f"{value / gib:.2f}GiB"
        return f"{value / mib:.2f}MiB"

    @staticmethod
    def _ensure_local_package_precedence():
        """
        优先加载根目录 `mlx-qwen3-asr` 子仓库源码。

        这是 P0 的导入路径保险：即使当前虚拟环境里还残留非 editable 安装副本，
        server worker 也会先把本地子仓库放到 sys.path 前面，保证本轮 Runner 改动实际生效。
        """
        project_root = Path(__file__).resolve().parents[4]
        local_package_root = project_root / "mlx-qwen3-asr"
        if not local_package_root.exists():
            return
        local_path = local_package_root.as_posix()
        if local_path not in sys.path:
            sys.path.insert(0, local_path)

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

        WorkPipeline 的 token 合并逻辑只要求 token/timestamp 对齐即可，
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
