#!/usr/bin/env python3
"""下载 CapsWriter Tech ASR Eval v1 所需的最小公开数据源。

这个脚本只负责把后续构建 manifest 所需的“源文件”落到本机，不在这里做
最终抽样、解压和音频转换。原因是部分公开数据集只提供压缩包或 parquet
shard，先把最小 shard 固定下来，后续 manifest 脚本才能做到完全可复现。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from huggingface_hub import hf_hub_download, snapshot_download


DATASET_ROOT = Path(__file__).resolve().parent
SOURCES_DIR = DATASET_ROOT / "sources"
TMP_DIR = DATASET_ROOT / "tmp"
LOG_PATH = TMP_DIR / "download_sources.log"
STATUS_PATH = TMP_DIR / "download_status.json"


@dataclass(frozen=True)
class DownloadItem:
    """单个待下载文件。

    repo_id / filename 固定 Hugging Face 源文件位置；local_subdir 固定本地落点。
    category 用来让命令行按阶段下载，避免一次性拉过多大文件。
    """

    name: str
    repo_id: str
    filename: str
    local_subdir: str
    category: str
    required_for_v1: bool
    note: str


@dataclass(frozen=True)
class SnapshotItem:
    """小型仓库快照下载项。

    Tech-Sentences 总量约百 MB，直接快照比逐个文件维护清单更稳；其它大数据集
    不使用 snapshot，避免误把全量音频拉下来。
    """

    name: str
    repo_id: str
    local_subdir: str
    allow_patterns: tuple[str, ...]
    category: str
    note: str


SNAPSHOTS: tuple[SnapshotItem, ...] = (
    SnapshotItem(
        name="tech_sentences_full",
        repo_id="danielrosehill/Tech-Sentences-For-ASR-Training",
        local_subdir="Tech-Sentences-For-ASR-Training",
        allow_patterns=(
            "README.md",
            "*.jsonl",
            "audio/*.wav",
            "text/*.txt",
        ),
        category="core",
        note="英文技术词汇源，小型仓库，保留完整音频和文本用于固定抽样。",
    ),
)


FILES: tuple[DownloadItem, ...] = (
    DownloadItem(
        name="aishell6_readme",
        repo_id="SMIIP-lab/AISHELL6-Whisper",
        filename="README.md",
        local_subdir="AISHELL6-Whisper",
        category="metadata",
        required_for_v1=True,
        note="中文真实低语数据集说明，gated 仓库需要登录并获批。",
    ),
    DownloadItem(
        name="aishell6_info_csv",
        repo_id="SMIIP-lab/AISHELL6-Whisper",
        filename="AISHELL6-Whisper_info.csv",
        local_subdir="AISHELL6-Whisper",
        category="metadata",
        required_for_v1=True,
        note="AISHELL6 样本信息表，用于后续抽取低语样本。",
    ),
    DownloadItem(
        name="aishell6_text_sentence",
        repo_id="SMIIP-lab/AISHELL6-Whisper",
        filename="text_sentence",
        local_subdir="AISHELL6-Whisper",
        category="metadata",
        required_for_v1=True,
        note="AISHELL6 转写文本。",
    ),
    DownloadItem(
        name="aishell6_w2n",
        repo_id="SMIIP-lab/AISHELL6-Whisper",
        filename="w2n.txt",
        local_subdir="AISHELL6-Whisper",
        category="metadata",
        required_for_v1=True,
        note="AISHELL6 文本规范化辅助表。",
    ),
    DownloadItem(
        name="aishell6_metadata_archive",
        repo_id="SMIIP-lab/AISHELL6-Whisper",
        filename="metadata.tar.gz",
        local_subdir="AISHELL6-Whisper",
        category="metadata",
        required_for_v1=True,
        note="AISHELL6 附加元数据包。",
    ),
    DownloadItem(
        name="aishell6_test_audio",
        repo_id="SMIIP-lab/AISHELL6-Whisper",
        filename="test.tar.gz",
        local_subdir="AISHELL6-Whisper",
        category="core",
        required_for_v1=True,
        note="中文真实低语最小音频 shard；不下载 train/valid 全量。",
    ),
    DownloadItem(
        name="chinese_lips_readme",
        repo_id="BAAI/Chinese-LiPS",
        filename="README.md",
        local_subdir="Chinese-LiPS",
        category="metadata",
        required_for_v1=True,
        note="Chinese-LiPS 数据集说明和许可。",
    ),
    DownloadItem(
        name="chinese_lips_meta_all",
        repo_id="BAAI/Chinese-LiPS",
        filename="meta_all.csv",
        local_subdir="Chinese-LiPS",
        category="metadata",
        required_for_v1=True,
        note="Chinese-LiPS 全量元数据，用于按 KJ 科技主题筛选。",
    ),
    DownloadItem(
        name="chinese_lips_meta_valid",
        repo_id="BAAI/Chinese-LiPS",
        filename="meta_valid.csv",
        local_subdir="Chinese-LiPS",
        category="metadata",
        required_for_v1=True,
        note="validation split 中 KJ 样本数足够，优先从这里抽 35 条。",
    ),
    DownloadItem(
        name="chinese_lips_processed_val",
        repo_id="BAAI/Chinese-LiPS",
        filename="processed_val.zip",
        local_subdir="Chinese-LiPS",
        category="core",
        required_for_v1=True,
        note="Chinese-LiPS 最小可用 16k 音频包；避免下载 4.3GB val.zip 或 86GB 全量。",
    ),
    DownloadItem(
        name="tedlium3_test_parquet",
        repo_id="AudioLLMs/tedlium3_test",
        filename="data/test-00000-of-00001.parquet",
        local_subdir="TED-LIUM3-test",
        category="core",
        required_for_v1=True,
        note="英文技术/科学演讲候选 shard，约 300MB，后续只抽 20 条。",
    ),
    DownloadItem(
        name="earnings22_chunked_small_shard",
        repo_id="distil-whisper/earnings22",
        filename="chunked/test-00010-of-00038-060854d670222f19.parquet",
        local_subdir="Earnings-22-chunked",
        category="core",
        required_for_v1=True,
        note="Earnings-22 较小 chunked shard，后续只抽 10 条商业技术口语样本。",
    ),
)


def configure_logging() -> None:
    """配置同时写文件和 stdout 的日志。

    stdout 只输出关键状态，完整 traceback 和每步细节都保留在日志文件中，避免
    Codex 对话上下文被下载进度刷满。
    """

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    # Hugging Face Hub 底层使用 httpx；如果根日志是 INFO，会把每个 HEAD/GET
    # 请求都打出来。这里把第三方库降到 WARNING，只保留本脚本的阶段性状态。
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)


def write_status(*, state: str, current: str | None, detail: str, finished: list[str], failed: list[str]) -> None:
    """写入机器可读状态文件，便于外部 monitor 低成本轮询。"""

    payload = {
        "state": state,
        "current": current,
        "detail": detail,
        "finished": finished,
        "failed": failed,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "log_path": str(LOG_PATH),
    }
    STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def local_file_for(item: DownloadItem) -> Path:
    """计算 hf_hub_download 在 local_dir 模式下的目标文件路径。"""

    return SOURCES_DIR / item.local_subdir / item.filename


def legacy_nested_file_for(item: DownloadItem) -> Path | None:
    """兼容早期脚本把 local_dir 设到 filename 父目录导致的嵌套文件。

    例如 filename 是 data/a.parquet，错误 local_dir=data 时，HF 会写到
    data/data/a.parquet。这里只用于复用已经下载完成的大文件，避免重复拉取。
    """

    filename_path = Path(item.filename)
    if filename_path.parent == Path("."):
        return None
    return SOURCES_DIR / item.local_subdir / filename_path.parent / item.filename


def has_existing_file(path: Path) -> bool:
    """判断源文件是否已经有效存在。

    只用 size > 0 做轻量判断；Hugging Face 下载本身支持断点续传，未完成文件
    会留在缓存中，重新运行脚本会继续补齐。
    """

    return path.exists() and path.is_file() and path.stat().st_size > 0


def selected_categories(args: argparse.Namespace) -> set[str]:
    """根据参数决定本轮下载阶段。"""

    if args.metadata_only:
        return {"metadata"}
    if args.core_only:
        return {"core"}
    return {"metadata", "core"}


def should_run_category(category: str, categories: set[str]) -> bool:
    """集中判断阶段过滤，避免下载计划在多处写分支。"""

    return category in categories


def download_snapshot(item: SnapshotItem, *, dry_run: bool) -> str:
    """下载小型 HF 仓库快照。"""

    target_dir = SOURCES_DIR / item.local_subdir
    logging.info("准备快照下载：%s -> %s；%s", item.repo_id, target_dir, item.note)
    if dry_run:
        return "dry-run"

    target_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=item.repo_id,
        repo_type="dataset",
        local_dir=target_dir,
        allow_patterns=list(item.allow_patterns),
        resume_download=True,
    )
    return "downloaded"


def download_file(item: DownloadItem, *, dry_run: bool) -> str:
    """下载单个 HF 文件，已有文件则跳过。"""

    target = local_file_for(item)
    logging.info("准备文件下载：%s/%s -> %s；%s", item.repo_id, item.filename, target, item.note)
    if has_existing_file(target):
        logging.info("跳过已存在文件：%s (%s bytes)", target, target.stat().st_size)
        return "skipped"
    legacy_target = legacy_nested_file_for(item)
    if legacy_target is not None and has_existing_file(legacy_target):
        logging.info("复用早期嵌套落点文件：%s (%s bytes)", legacy_target, legacy_target.stat().st_size)
        return "skipped-legacy-nested"
    if dry_run:
        return "dry-run"

    local_dir = SOURCES_DIR / item.local_subdir
    local_dir.mkdir(parents=True, exist_ok=True)
    returned_path = hf_hub_download(
        repo_id=item.repo_id,
        filename=item.filename,
        repo_type="dataset",
        local_dir=local_dir,
        resume_download=True,
    )
    returned = Path(returned_path)
    if not has_existing_file(target) and not has_existing_file(returned):
        raise RuntimeError(f"下载命令结束但目标文件不存在或为空：{target}；返回路径：{returned}")
    return "downloaded"


def iter_plan(categories: set[str]) -> Iterable[SnapshotItem | DownloadItem]:
    """按固定顺序输出下载计划。"""

    for item in SNAPSHOTS:
        if should_run_category(item.category, categories):
            yield item
    for item in FILES:
        if should_run_category(item.category, categories):
            yield item


def print_status() -> int:
    """打印当前状态和日志尾部，供人工或外部 monitor 查看。"""

    if STATUS_PATH.exists():
        print(STATUS_PATH.read_text(encoding="utf-8"))
    else:
        print(f"尚无状态文件：{STATUS_PATH}")
    if LOG_PATH.exists():
        print("\n--- log tail ---")
        lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-30:]:
            print(line)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载 CapsWriter Tech ASR Eval v1 的最小源数据。")
    parser.add_argument("--metadata-only", action="store_true", help="只下载 README/CSV/文本等元数据。")
    parser.add_argument("--core-only", action="store_true", help="只下载核心音频或 parquet shard。")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不实际下载。")
    parser.add_argument("--status", action="store_true", help="打印当前状态和日志尾部。")
    parser.add_argument("--fail-fast", action="store_true", help="遇到第一个失败项就退出；默认记录失败并继续下载其它源。")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_logging()
    if args.status:
        return print_status()

    if args.metadata_only and args.core_only:
        raise SystemExit("--metadata-only 和 --core-only 不能同时使用")

    categories = selected_categories(args)
    plan = list(iter_plan(categories))
    finished: list[str] = []
    failed: list[str] = []
    started_at = time.monotonic()
    write_status(state="running", current=None, detail="开始下载", finished=finished, failed=failed)

    logging.info("下载阶段：%s", ",".join(sorted(categories)))
    logging.info("数据根目录：%s", DATASET_ROOT)
    logging.info("HF_HOME=%s", os.environ.get("HF_HOME", "未设置，使用默认缓存"))
    logging.info("计划项数量：%s", len(plan))

    for item in plan:
        current_name = item.name
        write_status(state="running", current=current_name, detail="下载中", finished=finished, failed=failed)
        try:
            if isinstance(item, SnapshotItem):
                result = download_snapshot(item, dry_run=args.dry_run)
            else:
                result = download_file(item, dry_run=args.dry_run)
            finished.append(f"{current_name}:{result}")
            write_status(state="running", current=current_name, detail=f"完成：{result}", finished=finished, failed=failed)
        except Exception:
            logging.exception("下载失败：%s", current_name)
            failed.append(current_name)
            write_status(state="failed", current=current_name, detail="下载失败，详见日志", finished=finished, failed=failed)
            if args.fail_fast:
                return 1
            logging.info("继续下载后续项目；失败项已记录：%s", current_name)

    elapsed = time.monotonic() - started_at
    if failed:
        detail = f"可继续项目已处理完成，用时 {elapsed:.1f}s；失败 {len(failed)} 项"
        final_state = "partial"
    else:
        detail = f"全部完成，用时 {elapsed:.1f}s"
        final_state = "complete"
    logging.info(detail)
    write_status(state=final_state, current=None, detail=detail, finished=finished, failed=failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
