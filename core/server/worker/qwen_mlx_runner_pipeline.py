# coding: utf-8
"""
Qwen3-ASR MLX Runner 处理管线

这条管线只服务 `qwen_asr_mlx` 后端：Server Worker 不再把音频按 60 秒
语义分片交给旧 WorkPipeline 拼接，而是把同一个 task_id 的音频增量持续喂给
package 内的 QwenASRRunner。final 到达后，Runner 返回完整结果，再进入
CapsWriter 的最终格式化与发送流程。
"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np

from core.server.formatter import TextFormatter
from core.server.schema import Result, Work
from core.server.state import WorkerState, console
from core.tools.token_sync import sync_tokens_from_text
from . import logger


class QwenMLXRunnerPipeline:
    """面向 Qwen3-ASR Runner 的 Worker 侧薄适配层。"""

    def __init__(self, recognizer, punc_model=None, aligner=None, state: WorkerState = None):
        self.recognizer = recognizer
        self.punc_model = punc_model
        self.aligner = aligner
        self.formatter = TextFormatter(punc_model)
        self.state = state or WorkerState()

    def process(self, work: Work) -> Optional[Result]:
        """
        处理一个音频增量。

        非 final 增量只进入 Runner 缓冲，不向主进程返回识别消息；final 增量触发
        Runner 完成该 task_id 的完整离线结果，并返回 CapsWriter Result。
        """
        # submit来自主进程，wall clock用于跨进程排队时间；本进程处理耗时
        # 使用单调时钟。只有final输出汇总，避免将等包积压误报为模型变慢。
        queue_wait = max(0.0, time.time() - work.time_submit)
        processing_started = time.perf_counter()
        session = self.state.get_session(work.task_id, work.socket_id, work.source)
        result = session.result
        result.time_start = work.time_start
        result.time_submit = work.time_submit

        samples = np.frombuffer(work.data, dtype=np.float32)
        logger.debug(
            f"Qwen MLX Runner 收到音频增量: task={work.task_id[:8]}, "
            f"samples={len(samples)}, final={work.is_final}, source={work.source}"
        )

        runner_result = self.recognizer.feed_audio_patch(
            task_id=work.task_id,
            audio=samples,
            sample_rate=work.samplerate,
            is_final=work.is_final,
            context=work.context,
            language=work.language,
            source=work.source,
        )
        if runner_result is None:
            return None

        processing_time = time.perf_counter() - processing_started
        result.time_complete = time.time()
        result.duration = float(runner_result.duration)
        result.text = runner_result.text
        result.text_accu = runner_result.text
        result.is_final = True

        raw_text = result.text
        logger.info(f'模型输出：{raw_text}')
        result.text = self.formatter.format(result.text)
        result.text_accu = self.formatter.format(result.text_accu)

        console.print(f'  Qwen Runner 输出：[cyan]{raw_text}', soft_wrap=True)
        console.print(f'  格式化后：[green]{result.text}\n', soft_wrap=True)
        process_time = result.time_complete - work.time_submit
        rtf = process_time / result.duration if result.duration > 0 else 0
        logger.info(
            f"工作单元完成: {work.task_id[:8]}, 引擎=qwen_asr_mlx_runner, "
            f"时长={result.duration:.2f}s, 耗时={process_time:.3f}s, RTF={rtf:.3f}, "
            f"final排队={queue_wait:.3f}s, final处理={processing_time:.3f}s, "
            f"finish_reason={runner_result.finish_reason}, truncated={runner_result.truncated}"
        )

        # Runner 当前默认不返回字级时间戳。为了保持客户端协议兼容，沿用旧管线的
        # text fallback：按最终文本长度均匀生成代表性 timestamps。
        self._fill_fallback_tokens(result)
        result.tokens, result.timestamps = sync_tokens_from_text(
            result.tokens,
            result.timestamps,
            result.text_accu,
        )
        return result

    def cleanup_tasks(self, stale_task_ids: list[str]) -> None:
        """Worker 清理断连 session 时，同步释放 Runner 内部缓冲。"""
        for task_id in stale_task_ids:
            self.recognizer.cancel_task(task_id)

    @staticmethod
    def _fill_fallback_tokens(result: Result) -> None:
        """没有原生 timestamps 时，为客户端生成可用的字符级占位时间戳。"""
        if result.tokens or not result.text_accu:
            return
        chars = list(result.text_accu.replace(' ', ''))
        if not chars:
            return
        if result.duration <= 0:
            result.tokens = chars
            result.timestamps = [0.0 for _ in chars]
            return

        time_per_char = result.duration / len(chars)
        result.tokens = chars
        result.timestamps = [i * time_per_char for i in range(len(chars))]
