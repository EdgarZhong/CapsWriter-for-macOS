#!/usr/bin/env python3
"""在独立进程验证Qwen权重常驻，不重启日常服务、不注入内存压力、不定时推理。"""

from __future__ import annotations

import argparse
import ctypes
import json
from pathlib import Path
import re
import resource
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
# 与服务端相同，优先使用仓库内fork，避免误测site-packages旧副本。
sys.path.insert(0, str(ROOT / "mlx-qwen3-asr"))
sys.path.insert(0, str(ROOT))


def emit(event: str, **fields) -> None:
    """逐行输出可保存的证据，避免只记录set_limit或mlock的返回值。"""
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def system_wired() -> int:
    """只用vm_stat观察系统；空闲期间不调用MLX，不偷偷唤醒GPU。"""
    report = subprocess.check_output(["vm_stat"], text=True)
    page = int(re.search(r"page size of (\d+)", report)[1])
    return int(re.search(r"Pages wired down:\s+(\d+)", report)[1]) * page


def weight_addresses(runner) -> list[tuple[str, int, int]]:
    """确认转录不会替换被锁定的参数buffer；只取原始地址，不复制/改写权重。"""
    from mlx.utils import tree_flatten
    result = []
    for name, array in tree_flatten(runner.session.model.parameters()):
        raw = memoryview(array).cast("B")
        result.append((name, ctypes.addressof(ctypes.c_char.from_buffer(raw)), raw.nbytes))
    return result


def main() -> None:
    """分别用on/off进程验证，避免两个模型相互争用内存污染对照。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("on", "off"), required=True)
    parser.add_argument("--idle-seconds", type=float, default=300)
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--audio", type=Path,
                        default=ROOT / "mlx-qwen3-asr/tests/fixtures/test_speech.wav")
    args = parser.parse_args()
    if args.idle_seconds < 0 or args.interval <= 0:
        parser.error("idle-seconds必须非负，interval必须为正")
    from config_server import Qwen3ASRMLXArgs
    from mlx_qwen3_asr import QwenASRRunner, CapsWriterRunnerConfig
    from mlx_qwen3_asr.audio import load_audio_np

    # 音频先载入，空闲后的耗时只度量转录，排除读音频文件的磁盘缓存影响。
    audio = load_audio_np(str(args.audio))
    emit("baseline", mode=args.mode, wired_bytes=system_wired())
    runner = QwenASRRunner(model=Qwen3ASRMLXArgs.model, config=CapsWriterRunnerConfig(
        enable_wired_memory=args.mode == "on"))
    try:
        emit("ready", runtime=runner.runtime_info(), wired_bytes=system_wired())
        addresses = weight_addresses(runner)
        def transcribe(label):
            before = resource.getrusage(resource.RUSAGE_SELF)
            start = time.perf_counter()
            result = runner.transcribe_audio(audio, task_id=label)
            elapsed = time.perf_counter() - start
            after = resource.getrusage(resource.RUSAGE_SELF)
            emit(label, seconds=elapsed, text=result.text, finish_reason=result.finish_reason,
                 major_faults=after.ru_majflt - before.ru_majflt)
            return result.text
        first = transcribe("before_idle")
        start = time.monotonic()
        while (remaining := args.idle_seconds - (time.monotonic() - start)) > 0:
            time.sleep(min(args.interval, remaining))
            emit("idle", seconds=round(time.monotonic() - start, 2), wired_bytes=system_wired())
        last = transcribe("after_idle")
        emit("checks", same_text=first == last, same_weight_buffers=addresses == weight_addresses(runner))
        if first != last or addresses != weight_addresses(runner):
            raise AssertionError("空闲前后转录正文或权重buffer变化")
    finally:
        runner.cleanup()
        # 解锁后仍保留模型引用，证明wired下降来自munlock而非整个模型进程退出。
        time.sleep(10)
        emit("unlocked", wired_bytes=system_wired(), runtime=runner.wired_memory_info)


if __name__ == "__main__":
    main()
