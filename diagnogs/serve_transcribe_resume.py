# coding: utf-8
"""
续传补漏脚本：扫描 diagnogs/assets 与 diagnogs/transcripts 的差集，
把还没有对应 Markdown 的录音补跑一遍，最后校验数量一致性。

典型场景：全量批量转写中途中断后，用本脚本续跑；
或跑完后有个别失败文件，重跑本脚本直到缺失数为 0。

用法（在项目根目录）:
    .venv/bin/python diagnogs/serve_transcribe_resume.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 脚本位于 diagnogs/ 下，先补上项目根目录以便 import 同目录的批量脚本
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'diagnogs'))

from serve_transcribe import ASSETS_DIR, TRANSCRIPTS_DIR, ServeTranscriber, collect_files  # noqa: E402


def main() -> None:
    all_files = collect_files()
    missing = [
        f for f in all_files
        if not (TRANSCRIPTS_DIR / (f.stem + '.md')).exists()
    ]

    print(f'assets 共 {len(all_files)} 条，已转写 {len(all_files) - len(missing)} 条，'
          f'缺失 {len(missing)} 条')
    if not missing:
        print('全部转写完成，无需补跑')
        return

    transcriber = ServeTranscriber()
    import asyncio
    stats = asyncio.run(transcriber.run(missing))
    print(f"补跑完成 done={stats['done']} 跳过={stats['skip']} "
          f"空结果={stats['empty']} 失败={stats['fail']}")

    # 终态校验：数量必须与音频总数一致
    done_md = len(list(TRANSCRIPTS_DIR.glob('*.md')))
    remaining = len(all_files) - done_md
    print(f'校验: transcripts 共 {done_md} 个 md，剩余未转写 {remaining} 条')
    if remaining > 0:
        print('仍有缺失，可再次运行本脚本续跑')
        sys.exit(1)


if __name__ == '__main__':
    main()
