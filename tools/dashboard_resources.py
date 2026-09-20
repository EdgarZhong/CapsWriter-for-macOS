#!/usr/bin/env python3
"""Dashboard 开发桥接：只检测资源；用户点击下载后才安装缺失依赖或模型。"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import uuid

VARIANTS = ("8bit", "4bit")


def model_complete(directory: Path, variant: str) -> bool:
    """核对必要元数据与 safetensors 长度，不加载权重、不占用 GPU。"""
    try:
        config = json.loads((directory / "config.json").read_text())
        for name in ("tokenizer_config.json", "preprocessor_config.json"):
            json.loads((directory / name).read_text())
        if not (directory / "tokenizer.json").is_file():
            json.loads((directory / "vocab.json").read_text())
            if (directory / "merges.txt").stat().st_size == 0:
                return False
        weights = list(directory.glob("*.safetensors"))
        if not weights:
            return False
        index = directory / "model.safetensors.index.json"
        if index.exists():
            required = set(json.loads(index.read_text())["weight_map"].values())
            if not required or not required.issubset({p.name for p in weights}):
                return False
        for weight in weights:
            with weight.open("rb") as stream:
                header_length = struct.unpack("<Q", stream.read(8))[0]
                if not 2 <= header_length <= 100_000_000:
                    return False
                header = json.loads(stream.read(header_length))
            offsets = [v["data_offsets"][1] for k, v in header.items() if k != "__metadata__"]
            if not offsets or weight.stat().st_size != 8 + header_length + max(offsets):
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError, AttributeError, struct.error):
        return False


def runtime_ready(root: Path) -> bool:
    """真正导入依赖检测动态库缺失，仍不创建模型或推理实例。"""
    python = root / ".venv/bin/python"
    if not python.exists():
        return False
    try:
        result = subprocess.run(
            [str(python), "-c", "import mlx.core; import mlx_qwen3_asr; import huggingface_hub"],
            cwd=root, capture_output=True, timeout=30,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def inspect(root: Path) -> dict:
    runtime = runtime_ready(root)
    rows = []
    for variant in VARIANTS:
        directory = root / "models/Qwen3-ASR-MLX" / f"Qwen3-ASR-1.7B-{variant}"
        warning = None
        try:
            config = json.loads((directory / "config.json").read_text())
            bits = config.get("quantization", config.get("quantization_config", {})).get("bits")
            if bits is not None and bits != int(variant[0]):
                warning = f"本地配置标记为 {bits}-bit，与此条目规格不一致；请核对资源。"
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        rows.append({"id": variant, "directory": str(directory),
                     "modelReady": model_complete(directory, variant), "runtimeReady": runtime,
                     "warning": warning})
    return {"plans": rows}


def find_uv() -> str:
    """GUI 环境可能没有 shell PATH，仅搜索常用安装位置，不执行任意配置。"""
    candidates = [shutil.which("uv"), str(Path.home() / ".local/bin/uv"), "/opt/homebrew/bin/uv", "/usr/local/bin/uv"]
    for candidate in candidates:
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    raise RuntimeError("开发环境缺少 uv，请先按开发文档安装依赖。")


def download(root: Path, variant: str) -> None:
    """先下载到临时目录并验证，再备份替换；失败不会破坏现有模型。"""
    python = root / ".venv/bin/python"
    if not runtime_ready(root):
        uv = find_uv()
        if not python.exists():
            subprocess.run([uv, "venv", "--python", "3.13", str(root / ".venv")], check=True, cwd=root)
        subprocess.run([uv, "pip", "install", "--python", str(python),
                        str(root / "mlx-qwen3-asr"), "huggingface-hub"], check=True, cwd=root)
        if not runtime_ready(root):
            raise RuntimeError("运行时安装后检查未通过，请查看下载日志。")
    target = root / "models/Qwen3-ASR-MLX" / f"Qwen3-ASR-1.7B-{variant}"
    if model_complete(target, variant):
        return
    staging = root / ".archive" / f"model-download-{variant}-{uuid.uuid4().hex}"
    staging.mkdir(parents=True)
    # 参数通过 argv 传入固定 Python 程序，避免 shell 插值和路径转义问题。
    script = "from huggingface_hub import snapshot_download; import sys; snapshot_download(repo_id=sys.argv[1], local_dir=sys.argv[2], allow_patterns=[\"*.json\",\"*.safetensors\",\"*.txt\",\"*.model\"])"
    # 国内镜像是产品默认；显式环境覆盖仍可用于用户自选端点或故障排查。
    environment = dict(os.environ)
    environment.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    subprocess.run([str(python), "-c", script, f"mlx-community/Qwen3-ASR-1.7B-{variant}", str(staging)],
                   check=True, cwd=root, env=environment)
    if not model_complete(staging, variant):
        raise RuntimeError("下载资源不完整；已保留下载目录供排查，现有模型未替换。")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.rename(root / ".archive" / f"model-before-download-{variant}-{uuid.uuid4().hex}")
    staging.rename(target)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["inspect", "download", "start"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--variant", choices=VARIANTS)
    args = parser.parse_args()
    try:
        if args.action == "start":
            subprocess.run([str(args.root / ".venv/bin/python"), str(args.root / "capswriter.py"), "start"], cwd=args.root, check=True)
        if args.action == "download":
            if args.variant is None:
                raise ValueError("下载需要指定模型规格。")
            download(args.root.resolve(), args.variant)
        print(json.dumps(inspect(args.root.resolve()), ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
