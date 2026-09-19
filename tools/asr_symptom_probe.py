"""对两个已知症状做有限诊断；只在独立进程改变提示前缀，不修改生产配置。

默认只生成固定选样清单；--infer 才加载一次模型。每次创建独占输出目录，
逐条保存原始生成证据，Windows 文本仅作对照而非人工真值。
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import random
import re
import subprocess
import sys
import time
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mlx-qwen3-asr"))


def normalized(text):
    """沿用既有格式归一化规则，不合并同义词或改写语言。"""
    return "".join(c for c in text.lower() if not c.isspace()
                   and unicodedata.category(c)[0] not in "PSZ")


def transcript(path):
    """只读取 text 字段，防止把 text_accu 重复计入。"""
    content = path.read_text(encoding="utf-8-sig")
    text = content.split("## 转写文本 (text)", 1)[1].split("\n## ", 1)[0].strip()
    seconds = float(re.search(r"音频时长: ([\d.]+)", content).group(1))
    return "" if text == "（空）" else text, seconds


def write_json(path, value):
    """仅写当前新建目录内的诊断结果。"""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def select_cases():
    """风险选样与正常对照分组保存，禁止将这组结果推算为总体准确率。"""
    rows = []
    for p in sorted((ROOT / "diagnogs/transcripts").glob("*.md")):
        mac, seconds = transcript(p)
        windows, _ = transcript(ROOT / "diagnogs/transcripts_windows" / p.name)
        rows.append(dict(id=p.stem, mac=mac, windows=windows, duration=seconds))
    anomaly_ids = {"20260908-233752", "20260911-114212", "20260912-110033"}
    selected = [{**r, "group": "language_anomaly"} for r in rows if r["id"] in anomaly_ids]
    pools = {"chinese_control": [], "english_control": [], "mixed_control": []}
    for r in rows:
        text = normalized(r["mac"])
        if not 3 <= r["duration"] < 15 or text != normalized(r["windows"]):
            continue
        han = sum("\u4e00" <= c <= "\u9fff" for c in text)
        latin = len(re.findall("[a-z]", text))
        group = ("chinese_control" if han >= 15 and latin == 0 else
                 # 现有语料的全英文主要为短术语，这组不能代表长英文句子的准确率。
                 "english_control" if han == 0 and latin >= 15 else
                 "mixed_control" if han >= 8 and latin >= 15 else None)
        if group:
            pools[group].append(r)
    rng = random.Random(20260918)
    for group, pool in pools.items():
        if len(pool) < 3:
            raise ValueError(f"正常对照不足: {group}")
        selected.extend({**r, "group": group} for r in rng.sample(pool, 3))
    # 严格前缀只是尾缀差异候选，末字可能为补全或幻觉，不能直接称为漏识别真值。
    tails = [{**r, "group": "tail_candidate"} for r in rows
             if normalized(r["mac"]) and normalized(r["mac"]) != normalized(r["windows"])
             and normalized(r["windows"]).startswith(normalized(r["mac"]))]
    return selected + tails


def infer(cases, output, tail_followup=False, padding_controls=False, production_regression=False):
    """串行通过现有 Runner 识别，进程内钩子只记录生成并隔离正文前缀变量。"""
    import numpy as np
    from mlx_qwen3_asr import Session, QwenASRRunner, CapsWriterRunnerConfig
    from mlx_qwen3_asr.generate import resolve_max_new_tokens
    from core.client.transcribe.media_tool import MediaTool
    from core.server.formatter import TextFormatter

    model = ROOT / "models/Qwen3-ASR-MLX/Qwen3-ASR-1.7B-8bit"
    if production_regression:
        # 验收直接创建正式服务端适配器，并经 feed_audio_patch 分包进入 Runner。
        # 不替换提示词方法，防止测试钩子掩盖生产参数漏传；只关闭资源预热。
        from core.server.engines.qwen_asr_mlx.asr_engine import ASREngineConfig, QwenASRMLXEngine
        engine = QwenASRMLXEngine(ASREngineConfig(
            model=str(model), enable_startup_prewarm=False, enable_wired_memory=False))
        runner = engine.runner
        session = runner.session
        assert runner.config.auto_language_text_only is True
    else:
        session = Session(model=str(model))
        # 历史诊断的 auto 必须明确关闭新默认，否则会误把修复后结果当旧基线。
        runner = QwenASRRunner(str(model), session=session, config=CapsWriterRunnerConfig(
            enable_startup_prewarm=False, enable_wired_memory=False,
            auto_language_text_only=False))
    formatter = TextFormatter()
    module = importlib.import_module("mlx_qwen3_asr.transcribe")
    original_generate = module.generate
    original_prompt = session.tokenizer.build_prompt_tokens
    traces = []

    def traced_generate(**kwargs):
        """每段保存真正生效的配置与原始 token，而不是只保存解析后的结果。"""
        result = original_generate(**kwargs)
        traces.append({"prompt_tokens": kwargs["input_ids"].tolist(),
                       "config": asdict(kwargs["config"]), **asdict(result),
                       "raw_decode": session.tokenizer.decode(result.tokens)})
        return result

    def text_only_prompt(n_audio_tokens, language=None, context=""):
        """只追加正文分隔符，保留原模板与空 system，隔离语言前缀影响。"""
        assert language is None
        return original_prompt(n_audio_tokens, language=None, context=context) + session.tokenizer.encode("<asr_text>")

    module.generate = traced_generate
    metadata = {"versions": {n: importlib.metadata.version(n) for n in ["mlx", "numpy", "mlx-qwen3-asr"]},
                "model": str(model), "quantization": dict(Counter(int(m.bits) for _, m in session.model.named_modules() if hasattr(m, "bits"))),
                "main_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "package_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT / "mlx-qwen3-asr", text=True).strip(),
                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "runner_config": asdict(runner.config), "windows_is_ground_truth": False,
                "tail_followup": tail_followup, "padding_controls": padding_controls,
                "production_regression": production_regression}
    write_json(output / "runtime.json", metadata)
    files = {p.stem: p for p in (ROOT / "diagnogs/assets").iterdir() if p.suffix.lower() in {".mp3", ".wav"}}
    results = []
    try:
        for i, case in enumerate(cases):
            raw = subprocess.run(MediaTool.build_ffmpeg_cmd(files[case["id"]]), check=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
            pcm = np.frombuffer(raw, dtype=np.float32)
            # 末端能量仅描述声学边界，不能证明最后音素完整；保存完整音频散列便于追溯。
            tail = pcm[-3200:]
            row = {**case, "audio_file": str(files[case["id"]]), "samples": len(pcm),
                   "pcm_sha256": hashlib.sha256(raw).hexdigest(),
                   "last_200ms_rms": float(np.sqrt(np.mean(tail ** 2))), "runs": {}}
            modes = ["auto"] if case["group"] == "tail_candidate" else ["auto", "chinese", "text_only"]
            if production_regression:
                modes = ["auto", "production"]
            if tail_followup or padding_controls:
                # 所有候选均短于30秒，固定原始时长对应的预算，避免补静音同时扩大预算。
                assert len(pcm) / 16000 + 0.15 < 30
                budget = resolve_max_new_tokens(None, audio_duration_sec=len(pcm) / 16000)
                runner.config = replace(runner.config, max_new_tokens=budget)
                modes = ["auto", "pad_150ms"] if padding_controls else ["auto", "text_only", "pad_150ms"]
            for mode in modes:
                traces = []
                session.tokenizer.build_prompt_tokens = text_only_prompt if mode == "text_only" else original_prompt
                started = time.monotonic()
                # 补静音只作为诊断变量，不能补回已经遗失的真实音素，更不写回原录音。
                input_pcm = np.concatenate([pcm, np.zeros(2400, dtype=np.float32)]) if mode == "pad_150ms" else pcm
                if production_regression:
                    runner.config = replace(runner.config, auto_language_text_only=mode == "production")
                    # 包界与录音语义无关；非 final 包不得触发结果，最后一个包包含真实尾音。
                    for start in range(0, len(input_pcm), 4096):
                        final = start + 4096 >= len(input_pcm)
                        result = engine.feed_audio_patch(
                            task_id=f"{case['id']}-{mode}", audio=input_pcm[start:start + 4096],
                            sample_rate=16000, is_final=final, language=None, source="file")
                        assert final or result is None
                    assert result is not None
                else:
                    result = runner.transcribe_audio(input_pcm, task_id=f"{case['id']}-{mode}",
                        language="Chinese" if mode == "chinese" else None, source="file")
                row["runs"][mode] = {**asdict(result), "formatted": formatter.format(result.text),
                                      "elapsed": time.monotonic() - started, "generations": traces,
                                      "input_samples": len(input_pcm),
                                      "input_sha256": hashlib.sha256(input_pcm.tobytes()).hexdigest()}
            row["baseline_matches_saved"] = row["runs"]["auto"]["formatted"] == case["mac"]
            results.append(row)
            with (output / "results.jsonl").open("a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"{i+1}/{len(cases)} {case['id']} {case['group']} saved={row['baseline_matches_saved']} "
                  f"stop={row['runs']['auto']['finish_reason']}", flush=True)
    finally:
        session.tokenizer.build_prompt_tokens = original_prompt
        module.generate = original_generate
        runner.cleanup()
    write_json(output / "summary.json", {"cases": len(results),
        "baseline_matches_saved": sum(r["baseline_matches_saved"] for r in results),
        "baseline_finish_reasons": dict(Counter(r["runs"]["auto"]["finish_reason"] for r in results))})


def main():
    """先固定清单后加载模型；已存在的输出目录一律拒绝覆盖。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--infer", action="store_true")
    parser.add_argument("--tail-followup", action="store_true", help="仅检查尾缀候选，固定预算分别换前缀/补静音")
    parser.add_argument("--padding-controls", action="store_true", help="对原语言样本仅检查补静音的副作用")
    parser.add_argument("--production-regression", action="store_true", help="经正式适配器分包对比修复前后，不替换提示词")
    parser.add_argument("--output", type=Path, default=ROOT / "diagnogs/symptom_probe" / datetime.now().strftime("%Y%m%d-%H%M%S"))
    args = parser.parse_args()
    if sum([args.tail_followup, args.padding_controls, args.production_regression]) > 1:
        parser.error("后续诊断阶段不能同时选择")
    args.output.mkdir(parents=True, exist_ok=False)
    cases = select_cases()
    if args.tail_followup:
        cases = [c for c in cases if c["group"] == "tail_candidate"]
    elif args.padding_controls:
        cases = [c for c in cases if c["group"] != "tail_candidate"]
    write_json(args.output / "cases.json", cases)
    print(f"输出: {args.output}; 分组: {dict(Counter(c['group'] for c in cases))}", flush=True)
    if args.infer:
        infer(cases, args.output, tail_followup=args.tail_followup, padding_controls=args.padding_controls,
              production_regression=args.production_regression)


if __name__ == "__main__":
    main()
