#!/usr/bin/env python3
"""历史检索与修订桥接：保留原日记，只通过既有 AnnotationService 追加 v2。"""
from __future__ import annotations

import argparse
from datetime import datetime
import fcntl
import hashlib
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from urllib.parse import unquote

ENTRY = re.compile(r"^(?:\[(\d{2}:\d{2}:\d{2})\]\(([^\n]*)\)|(?:(\d{2}:\d{2}:\d{2}))) (.*?)(?=^(?:\[\d{2}:\d{2}:\d{2}\]\(|\d{2}:\d{2}:\d{2} )|\Z)", re.M | re.S)

# 界面只展示最近 200 条；更早的记录既不渲染也不参与搜索，避免长年日记拖垮窗口。
HISTORY_LIMIT = 200


def revision(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def records(root: Path, limit: int = HISTORY_LIMIT) -> list[dict]:
    """只读年月日目录和 v2 标注，不扫描归档、日志或其他个人文件。
    日记按日期文件从新到旧扫描，凑够 limit 条即停，不读取更早的文件。"""
    entries = []
    for path in sorted(root.glob("[12][0-9][0-9][0-9]/[01][0-9]/[0-3][0-9].md"), reverse=True):
        if len(entries) >= limit:
            break
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        # 文件内条目按时间顺序追加，倒序遍历保证先取当天最新记录。
        for match in reversed(list(ENTRY.finditer(content))):
            if len(entries) >= limit:
                break
            text = match[4].rstrip()
            clock = match[1] or match[3]
            identity = "diary:" + hashlib.sha256(f"{path.relative_to(root)}:{match.start()}".encode()).hexdigest()
            audio = (path.parent / unquote(match[2])).resolve() if match[2] else None
            # 历史链接仅允许指向本项目音频，不能借 UI 编辑复制任意外部文件。
            if audio is not None and (not audio.is_relative_to(root.resolve()) or audio.suffix.lower() not in {".mp3", ".wav", ".m4a", ".flac"}):
                audio = None
            entries.append({"id": identity, "date": f"{path.parent.parent.name}-{path.parent.name}-{path.stem} {clock}",
                            "text": text, "source": "日记", "raw": None, "audio": str(audio) if audio else None,
                            "duration": None, "task_id": identity, "source_app": None})
    # 已有标注以 task_id 聚合；后续人工修订与不可靠标记按追加顺序生效。
    annotations = root / "evals/manual_cases/v2/cases.jsonl"
    cases, edits = {}, {}
    if annotations.exists():
        for line in annotations.read_text(encoding="utf-8").splitlines():
            try:
                case = json.loads(line)
                if case.get("annotation_version") != 2:
                    continue
                if case.get("history_id"):
                    edits[case["history_id"]] = case
                elif case.get("task_id"):
                    cases[case["task_id"]] = case
            except (ValueError, AttributeError):
                continue
    for task, case in cases.items():
        audio = (annotations.parent / case["audio_file"]).resolve() if case.get("audio_file") else None
        if audio is not None and not audio.is_relative_to(annotations.parent.resolve()):
            audio = None
        entries.append({"id": f"annotation:{task}", "date": case.get("ts", "").replace("T", " "),
                        "text": case.get("final_text") if case.get("final_text") is not None else (case.get("raw_text") or ""),
                        "source": "已有标注", "raw": case.get("raw_text"), "audio": str(audio) if audio else None,
                        "duration": case.get("recording_duration"), "task_id": task, "source_app": case.get("source_app")})
    for entry in entries:
        edit = edits.get(entry["id"])
        entry["corrected"] = edit is not None
        if edit is not None:
            entry["text"] = edit.get("final_text") or ""
        entry["revision"] = revision(entry["text"])
        entry["hasAudio"] = bool(entry["audio"] and Path(entry["audio"]).is_file())
    # 合并日记与标注后统一截断到 limit，界面与搜索都不触及更早的记录。
    return sorted(entries, key=lambda item: (item["date"], item["id"]), reverse=True)[:limit]


def save(root: Path, request: dict) -> dict:
    """稳定 ID 重查原始记录并做冲突检查，调用方不能指定任意 raw 或音频路径。"""
    sys.path.insert(0, str(root))
    from core.client.output.annotation_store import AnnotationService
    lock_path = root / "evals/manual_cases/v2/.history.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        item = next((item for item in records(root) if item["id"] == request.get("id")), None)
        if item is None:
            raise ValueError("这条历史已找不到，请刷新列表。")
        if item["revision"] != request.get("revision"):
            raise ValueError("这条记录已被更新，请重新打开后编辑。")
        final = request.get("text")
        if not isinstance(final, str):
            raise ValueError("修订内容必须是文本。")
        if final == item["text"]:
            return {"saved": False, "message": "内容未改变，未新增标注。"}
        service = AnnotationService(SimpleNamespace(base_dir=root))
        # 微秒时间避免同一任务的连续修订覆盖既有音频备份。
        result = service.record({
            "ts": datetime.now().isoformat(timespec="microseconds"),
            "task_id": item["task_id"], "status": "corrected", "raw_text": item["raw"],
            "final_text": final, "recording_duration": item["duration"],
            "source_app": item["source_app"], "mode": "history", "kind": "editor_confirmed",
            "history_id": item["id"], "history_source": item["source"], "history_source_text": item["text"],
        }, audio_src=Path(item["audio"]) if item["hasAudio"] else None)
        if not result.get("write_ok"):
            raise ValueError("修订未能写入标注库，原记录未修改。")
        return {"saved": True, "message": "已保存修订并加入标注库。"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["list", "save"])
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        result = save(root, json.load(sys.stdin)) if args.action == "save" else {}
        # UI 只接收显示字段；原始路径与标注元数据留在桥接层。
        result["entries"] = [{k: item[k] for k in ("id", "date", "text", "source", "corrected", "revision", "hasAudio")} for item in records(root)]
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
