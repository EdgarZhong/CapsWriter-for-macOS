"""定位服务端尾部差异：只在独立进程切换卷积尾块补齐，不改生产模型实现。

Windows Mel 类直接从已核对源码提取，避免为了比较特征加载 ONNX/llama。
默认旧语言前缀用于复现既有 Mac 基线；--text-only 验证与已完成语言修复的组合。
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import asdict
from datetime import datetime
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mlx-qwen3-asr"))

import mlx.core as mx
import numpy as np

from mlx_qwen3_asr import CapsWriterRunnerConfig, QwenASRRunner, Session
from mlx_qwen3_asr.encoder import AudioEncoder
from core.client.transcribe.media_tool import MediaTool
from core.server.formatter import TextFormatter
from tools.asr_symptom_probe import normalized, select_cases, write_json


def windows_mel():
    """执行原类而不执行整个模块的可选依赖导入，保持算法源码原样。"""
    path = ROOT / "core/server/engines/qwen_asr_gguf/inference/encoder.py"
    tree = ast.parse(path.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "FastWhisperMel")
    namespace = {"np": np, "os": os}
    exec(compile(ast.Module(body=[cls], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["FastWhisperMel"](), hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    """串行记录同一 PCM 的基线与尾块补齐变体，输出目录禁止覆盖。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-only", action="store_true")
    parser.add_argument("--secondary", action="store_true", help="单独比较Windows Mel、提示词和全局注意力")
    parser.add_argument("--output", type=Path, default=ROOT / "diagnogs/tail_boundary" / datetime.now().strftime("%Y%m%d-%H%M%S"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    cases = select_cases()
    write_json(args.output / "cases.json", cases)
    print(f"输出: {args.output}", flush=True)
    session = Session(str(ROOT / "models/Qwen3-ASR-MLX/Qwen3-ASR-1.7B-8bit"))
    config = CapsWriterRunnerConfig(enable_startup_prewarm=False, enable_wired_memory=False,
                                   auto_language_text_only=args.text_only)
    runner = QwenASRRunner("unused", session=session, config=config)
    formatter = TextFormatter()
    tmod = importlib.import_module("mlx_qwen3_asr.transcribe")
    original_features, original_generate = tmod.compute_features, tmod.generate
    original_stem = AudioEncoder._apply_conv_stem
    emod = importlib.import_module("mlx_qwen3_asr.encoder")
    original_mask = emod._create_windowed_mask
    original_prompt = session.tokenizer.build_prompt_tokens
    win_mel, source_hash = windows_mel()
    write_json(args.output / "runtime.json", {"config": asdict(config), "windows_encoder_sha256": source_hash,
        "secondary": args.secondary, "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "encoder_sha256": hashlib.sha256((ROOT / "mlx-qwen3-asr/mlx_qwen3_asr/encoder.py").read_bytes()).hexdigest()})
    state = {}

    def features(pcm):
        """对比相同 PCM 的 Mel，保留 MLX 返回值，故此处不改变推理输入。"""
        mel, lens = original_features(pcm)
        actual = np.asarray(mel[0])
        other = win_mel(pcm)
        assert actual.shape == other.shape
        delta = np.abs(actual - other)
        state["frames"] = actual.shape[1]
        state["mel"] = {"frames": actual.shape[1], "max_abs": float(delta.max()),
                        "mean_abs": float(delta.mean()), "tail_max_abs": float(delta[:, -20:].max())}
        if state["mode"] == "windows_mel":
            # 特征shape与lens不变，只替换数值实现；其余两端不同点保持MLX原样。
            return mx.array(other[None, :, :]), lens
        return mel, lens

    def prompt(n_audio_tokens, language=None, context="", **kwargs):
        """逐token复刻Windows auto模板，隔离system文本和消息换行差异。"""
        if state["mode"] != "windows_prompt":
            return original_prompt(n_audio_tokens, language=language, context=context, **kwargs)
        tok = session.tokenizer
        prefix = [tok.IM_START_ID] + tok.encode(f"system\n{context or 'You are a helpful assistant.'}")
        prefix += [tok.IM_END_ID, tok.IM_START_ID] + tok.encode("user\n") + [tok.AUDIO_START_TOKEN_ID]
        suffix = [tok.AUDIO_END_TOKEN_ID, tok.IM_END_ID, tok.IM_START_ID]
        suffix += tok.encode("assistant\n" + (f"language {language}" if language else ""))
        return prefix + [tok.AUDIO_TOKEN_ID] * n_audio_tokens + suffix + tok.encode("<asr_text>")

    def mask(seq_len, cu_seqlens, dtype=mx.float32):
        """Windows ONNX后端对有效帧全局注意；本批短于30秒，不走分窗优化分支。"""
        assert len(cu_seqlens) - 1 < emod._WINDOWED_SEGMENT_MIN_WINDOWS
        if state["mode"] == "global_attention":
            return None
        return original_mask(seq_len, cu_seqlens, dtype)

    def stem(encoder, x):
        """仅尾块补到官方 pad_sequence 的长度，输出仍裁回原有效帧数。

        一条不足100帧的短音频在Qwen原版中没有完整块，不会补到100；因此
        这里要求本条存在完整块。零值位于Mel域，不是往PCM追加150ms静音。
        """
        original = original_stem(encoder, x)
        width = int(x.shape[2])
        chunk_size = encoder.config.n_window * 2
        if width < chunk_size and state["frames"] > chunk_size:
            padded = mx.pad(x, [(0, 0), (0, 0), (0, chunk_size - width), (0, 0)])
            corrected = original_stem(encoder, padded)[:, :, :original.shape[2], :]
            diff = np.abs(np.asarray(corrected).astype(np.float32) - np.asarray(original).astype(np.float32))
            state["conv_tail"] = {"input_frames": width, "output_frames": int(original.shape[2]),
                "max_abs_per_time": diff.max(axis=(0, 1, 3)).tolist()}
            if state["mode"] == "pad_conv_tail":
                return corrected
        return original

    def generate(**kwargs):
        """保存有效音频token数与生成证据，防止补齐悄悄增加模型可见时长。"""
        result = original_generate(**kwargs)
        state["generation"] = {**asdict(result), "config": asdict(kwargs["config"]),
            "prompt_tokens": kwargs["input_ids"].tolist(), "audio_shape": list(kwargs["audio_features"].shape),
            "raw_decode": session.tokenizer.decode(result.tokens)}
        return result

    tmod.compute_features, tmod.generate = features, generate
    AudioEncoder._apply_conv_stem = stem
    session.tokenizer.build_prompt_tokens = prompt
    emod._create_windowed_mask = mask
    files = {p.stem: p for p in (ROOT / "diagnogs/assets").iterdir() if p.suffix.lower() in {".wav", ".mp3"}}
    results = []
    try:
        for index, case in enumerate(cases):
            raw = subprocess.run(MediaTool.build_ffmpeg_cmd(files[case["id"]]), check=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
            pcm = np.frombuffer(raw, dtype=np.float32)
            row = {**case, "pcm_sha256": hashlib.sha256(raw).hexdigest(), "runs": {}}
            modes = ["baseline", "windows_mel", "windows_prompt", "global_attention"] if args.secondary else ["baseline", "pad_conv_tail"]
            for mode in modes:
                state = {"mode": mode}
                result = runner.transcribe_audio(pcm, task_id=f"{case['id']}-{mode}")
                row["runs"][mode] = {**asdict(result), "formatted": formatter.format(result.text), **state}
            row["baseline_matches_saved"] = row["runs"]["baseline"]["formatted"] == case["mac"]
            row["changed"] = any(normalized(row["runs"]["baseline"]["formatted"]) != normalized(v["formatted"])
                                 for k, v in row["runs"].items() if k != "baseline")
            with (args.output / "results.jsonl").open("a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            results.append(row)
            print(f"{index+1}/{len(cases)} {case['id']} {case['group']} baseline={row['baseline_matches_saved']} changed={row['changed']}", flush=True)
    finally:
        tmod.compute_features, tmod.generate = original_features, original_generate
        AudioEncoder._apply_conv_stem = original_stem
        session.tokenizer.build_prompt_tokens = original_prompt
        emod._create_windowed_mask = original_mask
        runner.cleanup()
    write_json(args.output / "summary.json", {"cases": len(results),
        "baseline_matches_saved": sum(r["baseline_matches_saved"] for r in results),
        "changed": sum(r["changed"] for r in results)})


if __name__ == "__main__":
    main()
