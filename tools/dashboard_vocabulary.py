#!/usr/bin/env python3
"""Dashboard 词库桥接：列出与保存三个词库文件（hot.txt / hot-rule.txt / hot-server.txt）。

设计约束：
- 与 tools/dashboard_resources.py 同一约定：argparse action + --root，
   stdout 最后一行输出 JSON，异常时输出 {"error": ...} 且返回 1。
- save 采用整批校验、整批写盘：任一行不合法则拒绝全部，不写盘。
- 写盘前把原文件复制备份到 .archive/（带时间戳），绝不删除任何文件。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

# 三个受管词库文件：键为桥接协议中的稳定标识，值为仓库根目录下的真实文件名
FILES = {
    "hot": "hot.txt",
    "rule": "hot-rule.txt",
    "server": "hot-server.txt",
}

# 热词别名分隔符：客户端运行时只认半角 |（core/client/hotword/hot_phoneme.py），
# 但既有词库中混用了全角 ｜，解析时两者都接受，写回时统一规范为半角。
ALIAS_SEPARATORS = ("|", "｜")


def classify(line: str) -> str:
    """行分类：空行、注释行（# 开头）、条目行。"""
    stripped = line.strip()
    if not stripped:
        return "blank"
    if stripped.startswith("#"):
        return "comment"
    return "entry"


def split_hotword(line: str) -> tuple[str, list[str]]:
    """解析热词行：首个片段为目标词，其余为别名（两侧空白省略）。"""
    parts = [line]
    for separator in ALIAS_SEPARATORS:
        parts = [piece for part in parts for piece in part.split(separator)]
    pieces = [piece.strip() for piece in parts]
    pieces = [piece for piece in pieces if piece]
    return (pieces[0] if pieces else "", pieces[1:])


def split_rule(line: str) -> tuple[str | None, str | None]:
    """解析规则行：与运行时 hot_rule.py 一致，按 ' = ' 拆分且必须恰好两段。"""
    parts = line.split(" = ")
    if len(parts) != 2:
        return (None, None)
    return (parts[0].strip(), parts[1].strip())


def list_file(root: Path, key: str) -> dict:
    """读取单个词库文件并逐行解析；文件缺失不算错误，返回 exists=false。"""
    path = root / FILES[key]
    result: dict = {"exists": path.is_file(), "path": str(path), "entries": []}
    if not result["exists"]:
        return result
    # 保留原始行文本（去掉行尾换行），注释与空行也原样带出，供界面只读展示与回写
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, raw in enumerate(lines):
        kind = classify(raw)
        entry: dict = {"file": key, "index": index, "raw": raw, "kind": kind}
        if kind == "entry":
            if key == "rule":
                pattern, replacement = split_rule(raw)
                entry["pattern"] = pattern
                entry["replacement"] = replacement
            else:
                term, aliases = split_hotword(raw)
                entry["term"] = term
                entry["aliases"] = aliases
        result["entries"].append(entry)
    return result


def list_all(root: Path) -> dict:
    return {"files": {key: list_file(root, key) for key in FILES}}


def validate_lines(key: str, lines: list[str]) -> None:
    """整批校验待写入行；不合法则抛出 ValueError，调用方拒绝整批、不写盘。"""
    for number, line in enumerate(lines, start=1):
        if not isinstance(line, str):
            raise ValueError(f"第 {number} 行不是文本。")
        if "\n" in line or "\r" in line:
            raise ValueError(f"第 {number} 行包含换行符，无法写入。")
        if classify(line) != "entry":
            continue
        if key == "rule":
            pattern, _ = split_rule(line)
            if not pattern:
                raise ValueError(f"第 {number} 行不是合法的「查找模式 = 替换式」规则：{line}")
            try:
                re.compile(pattern)
            except re.error as error:
                raise ValueError(f"第 {number} 行左侧正则无法编译：{error}") from error
        else:
            term, _ = split_hotword(line)
            if not term:
                raise ValueError(f"第 {number} 行热词为空：{line}")


def save(root: Path) -> dict:
    """从 stdin 读取 {"file": ..., "lines": [...]}，校验通过后备份并整文件覆盖写入。"""
    payload = json.loads(sys.stdin.read())
    key = payload.get("file")
    if key not in FILES:
        raise ValueError(f"未知词库文件标识：{key!r}（可选：{', '.join(FILES)}）")
    lines = payload.get("lines")
    if not isinstance(lines, list):
        raise ValueError("lines 必须是字符串数组。")
    validate_lines(key, lines)

    path = root / FILES[key]
    # 写盘前备份原文件到 .archive/，时间戳避免相互覆盖；不删除任何文件
    if path.exists():
        archive = root / ".archive"
        archive.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, archive / f"{path.name}.{stamp}.bak")
    # UTF-8 写入，文件末尾保留恰好一个换行
    text = "\n".join(lines)
    if text:
        text += "\n"
    path.write_text(text, encoding="utf-8")

    result = list_all(root)
    result["saved"] = True
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["list", "save"])
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        if args.action == "list":
            print(json.dumps(list_all(root), ensure_ascii=False))
        else:
            print(json.dumps(save(root), ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
