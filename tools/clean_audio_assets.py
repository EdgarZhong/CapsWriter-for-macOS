#!/usr/bin/env python3
# coding: utf-8
"""
清理 CapsWriter 录音产物脚本

范围：项目根目录下按年归档的 assets 文件夹里的 .wav / .mp3 录音文件，
例如 2026/05/assets/、2026/06/assets/ 等（以及 1970/*/assets 兜底）。

默认 dry-run：只统计并打印将删除的文件数量与总大小，不实际删除。
加 --apply 才真正删除（rm）。

用法：
    python tools/clean_audio_assets.py            # dry-run，查看将删什么
    python tools/clean_audio_assets.py --apply    # 真正删除

安全约束：
    - 只处理 <项目根>/<四位年份>/*/assets/ 目录下的 .wav / .mp3
    - 绝不删除其它路径、其它扩展名
    - --apply 需要显式确认一次
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 项目根目录（脚本位于 tools/ 下）
ROOT = Path(__file__).resolve().parent.parent

# 只匹配四位数字年份目录下的 assets 子目录
ASSETS_GLOBS = ("????/*/assets", "????/*/*/assets")


def collect_files() -> list[Path]:
    """收集所有待删的录音文件（wav / mp3），按路径排序。"""
    found: list[Path] = []
    for pattern in ASSETS_GLOBS:
        for assets_dir in ROOT.glob(pattern):
            if not assets_dir.is_dir():
                continue
            for f in assets_dir.iterdir():
                if f.is_file() and f.suffix.lower() in (".wav", ".mp3"):
                    found.append(f)
    found.sort()
    return found


def format_size(num: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(num) < 1024.0 or unit == "TiB":
            return f"{num:.2f} {unit}"
        num /= 1024.0
    return f"{num:.2f} TiB"


def main() -> int:
    parser = argparse.ArgumentParser(description="清理 CapsWriter 录音产物（wav/mp3）")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正删除文件；不加此参数仅 dry-run 预览",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="dry-run 时打印全部文件路径（默认只打印统计）",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="跳过交互确认直接删除（用于非交互环境；请先 dry-run 确认范围）",
    )
    args = parser.parse_args()

    files = collect_files()

    if not files:
        print("未找到任何 .wav / .mp3 录音文件，无需清理。")
        return 0

    total_bytes = sum(f.stat().st_size for f in files)
    mode = "将删除" if args.apply else "将删除(dry-run 预览)"

    print(f"=== CapsWriter 录音清理预览 ===")
    print(f"目标目录: {ROOT}/<年份>/*/assets/")
    print(f"匹配文件: {len(files)} 个, 总大小: {format_size(total_bytes)}")
    print()

    # 按 <年份>/<月份> 汇总
    by_month: dict[Path, list[Path]] = {}
    for f in files:
        # 相对根目录，取前两段（年份/月份）拼成 Path
        rel = f.relative_to(ROOT)
        key = Path(rel.parts[0]) / rel.parts[1]
        by_month.setdefault(key, []).append(f)

    print(f"{mode}明细（按月）:")
    for month in sorted(by_month):
        m_files = by_month[month]
        m_bytes = sum(f.stat().st_size for f in m_files)
        n_wav = sum(1 for f in m_files if f.suffix.lower() == ".wav")
        n_mp3 = sum(1 for f in m_files if f.suffix.lower() == ".mp3")
        print(f"  {month}/assets : {len(m_files)} 个 (wav={n_wav}, mp3={n_mp3}), "
              f"{format_size(m_bytes)}")

    if args.list:
        print()
        print("文件清单:")
        for f in files:
            print(f"  {f.relative_to(ROOT)}")

    if not args.apply:
        print()
        print(f"[dry-run] 未做任何修改。确认无误后加 --apply 真正删除。")
        return 0

    # 真正删除，需显式确认（--yes 或交互输入 yes）
    print()
    if not args.yes:
        try:
            ans = input(f"将永久删除 {len(files)} 个文件（{format_size(total_bytes)}），"
                        f"不可恢复。输入 yes 继续: ")
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            return 1
        if ans.strip().lower() != "yes":
            print("已取消，未删除任何文件。")
            return 1

    deleted = 0
    freed = 0
    for f in files:
        try:
            sz = f.stat().st_size
            f.unlink()
            deleted += 1
            freed += sz
        except OSError as e:
            print(f"删除失败: {f} ({e})")
    print(f"完成: 删除 {deleted} 个文件, 释放 {format_size(freed)}。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
