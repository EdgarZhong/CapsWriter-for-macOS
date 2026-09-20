#!/usr/bin/env python3
"""Dashboard 设置桥接：列出白名单设置项，并把用户修改写入本机覆盖文件。

设计要点：
- 不在 GUI 进程里直接 import 配置，而是用仓库虚拟环境的 Python 子进程加载，
  保证拿到的「当前生效值」与真实客户端/服务端进程一致（含本机覆盖文件）。
- 保存时只做行级更新/追加，绝不整体重写本机覆盖文件，保留用户已有注释与非白名单行。
- 写入前把原覆盖文件备份到 .archive/，遵守仓库「永远不删除文件」的约定。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# 白名单字段表：仅收录 macOS 实际使用、且有把握解释清楚的配置。
# key 采用「组.类.属性」或「组.属性」路径，default 为发布默认值，
# 需要与 config_client.py / config_server.py 中的定义保持同步；
# default 同时充当配置导入失败时的回退值（此时标记 defaultOnly）。
# needsRestart 一律为 True：各字段运行时热加载行为未逐项验证，界面统一提示重启后生效。
FIELDS = [
    # ===== 客户端（config_client.py 的 ClientConfig）=====
    {"key": "client.threshold", "group": "client", "title": "快捷键触发阈值（秒）",
     "kind": "float", "default": 0.3,
     "help": "按住快捷键超过该时长才进入录音，短按仍补发原按键。"},
    {"key": "client.paste", "group": "client", "title": "模拟粘贴上屏",
     "kind": "bool", "default": False,
     "help": "识别结果写入剪贴板后模拟粘贴，适合不支持直接输入的应用。"},
    {"key": "client.restore_clip", "group": "client", "title": "粘贴后恢复剪贴板",
     "kind": "bool", "default": True,
     "help": "模拟粘贴上屏后，把剪贴板恢复到之前的内容。"},
    {"key": "client.save_audio", "group": "client", "title": "保存录音文件",
     "kind": "bool", "default": True,
     "help": "把每次听写的录音保存到本地目录，便于回溯与标注。"},
    {"key": "client.audio_name_len", "group": "client", "title": "录音文件名长度",
     "kind": "int", "default": 20,
     "help": "录音文件名取识别结果的前多少个字，建议不超过 200。"},
    {"key": "client.trash_punc", "group": "client", "title": "末尾标点剔除",
     "kind": "string", "default": "，。,.",
     "help": "识别结果末尾出现这些标点时自动去掉。"},
    {"key": "client.traditional_convert", "group": "client", "title": "繁体转换",
     "kind": "bool", "default": False,
     "help": "把识别结果从简体中文转换为繁体中文。"},
    {"key": "client.traditional_locale", "group": "client", "title": "繁体地区",
     "kind": "choice", "choices": ["zh-hant", "zh-tw", "zh-hk"], "default": "zh-hant",
     "help": "繁体转换的目标地区用词习惯。"},
    {"key": "client.hot", "group": "client", "title": "热词替换",
     "kind": "bool", "default": True,
     "help": "启用热词库匹配，把识别结果替换为热词表中的写法。"},
    {"key": "client.hot_thresh", "group": "client", "title": "热词替换阈值",
     "kind": "float", "default": 0.85,
     "help": "相似度超过该阈值的热词直接参与替换，越高越保守。"},
    {"key": "client.hot_similar", "group": "client", "title": "相似热词阈值",
     "kind": "float", "default": 0.6,
     "help": "相似度超过该阈值的热词作为上下文提示，辅助识别。"},
    {"key": "client.hot_rule", "group": "client", "title": "规则替换",
     "kind": "bool", "default": True,
     "help": "启用基于正则表达式的自定义替换规则。"},
    {"key": "client.editor_mode", "group": "client", "title": "编辑框标注模式",
     "kind": "bool", "default": True,
     "help": "识别结果先进编辑框，确认后再上屏；放弃时不入标注库。"},
    {"key": "client.macos_mic_device", "group": "client", "title": "录音设备",
     "kind": "choice", "choices": ["default", "builtin"], "default": "default",
     "help": "跟随系统默认输入，或固定优先使用 Mac 内建麦克风。"},
    {"key": "client.context", "group": "client", "title": "提示词上下文",
     "kind": "string", "default": "",
     "help": "填人名、地名、专业术语等，辅助模型识别相关词汇。"},
    {"key": "client.language", "group": "client", "title": "识别语言",
     "kind": "choice", "choices": ["auto", "chinese", "english", "japanese"], "default": "auto",
     "help": "限定识别语言；auto 表示由模型自动判断。"},
    {"key": "client.log_level", "group": "client", "title": "日志级别",
     "kind": "choice", "choices": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], "default": "DEBUG",
     "help": "客户端日志的详细程度。"},
    # ===== 识别服务（config_server.py 的 ServerConfig / Qwen3ASRMLXArgs）=====
    {"key": "server.format_num", "group": "server", "title": "中文数字转阿拉伯数字",
     "kind": "bool", "default": True,
     "help": "把识别结果中的中文数字改写为阿拉伯数字。"},
    {"key": "server.format_spell", "group": "server", "title": "中英空格调整",
     "kind": "bool", "default": True,
     "help": "自动调整中文与英文、数字之间的空格。"},
    {"key": "server.aligner_idle_timeout", "group": "server", "title": "对齐引擎空闲释放（秒）",
     "kind": "int", "default": 10,
     "help": "对齐引擎空闲该秒数后释放内存，0 表示常驻不释放。"},
    {"key": "server.log_level", "group": "server", "title": "服务端日志级别",
     "kind": "choice", "choices": ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], "default": "DEBUG",
     "help": "识别服务日志的详细程度。"},
    {"key": "server.Qwen3ASRMLXArgs.enable_startup_prewarm", "group": "server", "title": "启动预热",
     "kind": "bool", "default": True,
     "help": "服务启动时预热 MLX 模型，减少首次转录的等待。"},
    {"key": "server.Qwen3ASRMLXArgs.enable_wired_memory", "group": "server", "title": "锁定模型内存",
     "kind": "bool", "default": True,
     "help": "把模型权重锁定在内存中不被换出；锁页失败会拒绝启动。"},
    {"key": "server.Qwen3ASRMLXArgs.wired_memory_limit", "group": "server", "title": "锁页内存上限",
     "kind": "string", "default": "auto",
     "help": "锁页预算上限，auto 表示自动计算，也可填如 8G 的字节量。"},
]

FIELD_BY_KEY = {field["key"]: field for field in FIELDS}

# key 路径到（模块名, 类名, 属性名）的解析规则：
# client 组固定落在 ClientConfig；server 组带类名前缀时按前缀取类，否则落在 ServerConfig。
def resolve_target(key: str) -> tuple[str, str, str]:
    parts = key.split(".")
    if parts[0] == "client":
        return "config_client", "ClientConfig", parts[1]
    if len(parts) == 3:
        return "config_server", parts[1], parts[2]
    return "config_server", "ServerConfig", parts[1]


def find_python(root: Path, override: str | None) -> str:
    """优先用仓库虚拟环境的解释器加载配置，与真实运行进程保持一致。"""
    if override:
        return override
    venv_python = root / ".venv/bin/python"
    return str(venv_python) if venv_python.exists() else sys.executable


def load_effective_values(root: Path, python: str) -> dict[str, object] | None:
    """子进程 import 配置模块取生效值；任何失败都返回 None，由调用方回退默认值。"""
    targets: dict[str, dict[str, list[str]]] = {}
    for key in FIELD_BY_KEY:
        module, cls, attr = resolve_target(key)
        targets.setdefault(module, {}).setdefault(cls, []).append(attr)
    lines = ["import json, sys", "sys.path.insert(0, '.')", "out = {}"]
    for module, classes in targets.items():
        lines.append("try:")
        lines.append(f"    import {module}")
        for cls, attrs in classes.items():
            for attr in attrs:
                lines.append(f"    out[{module + '.' + cls + '.' + attr!r}] = "
                             f"getattr({module}.{cls}, {attr!r})")
        lines.append("except Exception:")
        lines.append("    pass")
    lines.append("print(json.dumps(out, ensure_ascii=False))")
    try:
        result = subprocess.run(
            [python, "-c", "\n".join(lines)], cwd=root,
            capture_output=True, timeout=60,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout.decode("utf-8").strip().splitlines()[-1])
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return None


def list_fields(root: Path, python: str) -> list[dict]:
    """组装字段元数据；导入失败的字段回退为发布默认值并标记 defaultOnly。"""
    effective = load_effective_values(root, python)
    rows = []
    for field in FIELDS:
        module, cls, attr = resolve_target(field["key"])
        lookup = f"{module}.{cls}.{attr}"
        row = {
            "key": field["key"],
            "group": field["group"],
            "title": field["title"],
            "kind": field["kind"],
            "value": field["default"],
            "default": field["default"],
            "needsRestart": True,
            "help": field["help"],
        }
        if "choices" in field:
            row["choices"] = field["choices"]
        if effective is None or lookup not in effective:
            # 导入失败：只能保证默认值准确，明确告知界面不要当作本机生效值。
            row["defaultOnly"] = True
        else:
            row["value"] = effective[lookup]
        rows.append(row)
    return rows


def validate(key: str, value: object) -> str | None:
    """白名单与类型校验；返回 None 表示通过，否则返回中文错误说明。"""
    field = FIELD_BY_KEY.get(key)
    if field is None:
        return f"不支持的设置项：{key}"
    kind = field["kind"]
    if kind == "bool":
        if not isinstance(value, bool):
            return f"{field['title']} 需要布尔值"
    elif kind == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            return f"{field['title']} 需要整数"
    elif kind == "float":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return f"{field['title']} 需要数值"
    elif kind == "string":
        if not isinstance(value, str):
            return f"{field['title']} 需要文本"
    elif kind == "choice":
        if not isinstance(value, str) or value not in field["choices"]:
            return f"{field['title']} 只能是：{'、'.join(field['choices'])}"
    return None


def literal(value: object) -> str:
    """生成覆盖文件里的 Python 字面量；布尔值用大写，字符串用 repr 保证可解析。"""
    if isinstance(value, bool):
        return "True" if value else "False"
    return repr(value)


LOCAL_HEADER = (
    "# 本机个人配置覆盖（已在 .gitignore 中忽略，不随发布入库）。\n"
    "# 由 {source} 末尾自动加载，也可由 Dashboard 设置页写入。\n"
)


def apply_overrides(path: Path, source: str, updates: dict[tuple[str, str], object], root: Path) -> None:
    """行级更新本机覆盖文件：命中已有赋值行则替换，否则追加，其他行原样保留。"""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    if not lines:
        lines = LOCAL_HEADER.format(source=source).splitlines()
    elif lines and lines[-1].strip():
        pass  # 追加前不强制空行，保持用户文件原貌
    written: set[tuple[str, str]] = set()
    for index, line in enumerate(lines):
        for (cls, attr), value in updates.items():
            if (cls, attr) in written:
                continue
            pattern = rf"^\s*{re.escape(cls)}\.{re.escape(attr)}\s*="
            if re.match(pattern, line) and not line.lstrip().startswith("#"):
                lines[index] = f"{cls}.{attr} = {literal(value)}"
                written.add((cls, attr))
    remaining = {target: value for target, value in updates.items() if target not in written}
    if remaining:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append(f"# 以下由 Dashboard 设置页于 {datetime.now():%Y-%m-%d %H:%M} 写入")
        for (cls, attr), value in remaining.items():
            lines.append(f"{cls}.{attr} = {literal(value)}")
    if path.exists():
        # 写入前备份原文件，备份永不删除，便于随时人工回滚。
        archive = root / ".archive"
        archive.mkdir(exist_ok=True)
        shutil.copy2(path, archive / f"{path.name}.bak-{datetime.now():%Y%m%d-%H%M%S}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def save(root: Path, python: str, payload: dict) -> dict:
    if not isinstance(payload, dict) or not payload:
        raise ValueError("保存内容为空或格式不正确。")
    for key, value in payload.items():
        problem = validate(key, value)
        if problem:
            raise ValueError(problem)
    # 按目标文件分组，全部校验通过后再落盘，避免只写一半。
    by_file: dict[Path, dict[tuple[str, str], object]] = {}
    for key, value in payload.items():
        module, cls, attr = resolve_target(key)
        local = root / ("config_client_local.py" if module == "config_client" else "config_server_local.py")
        by_file.setdefault(local, {})[(cls, attr)] = value
    for local, updates in by_file.items():
        apply_overrides(local, "config_client.py" if "client" in local.name else "config_server.py",
                        updates, root)
    groups = {FIELD_BY_KEY[key]["group"] for key in payload}
    names = "、".join("客户端" if group == "client" else "识别服务" for group in sorted(groups))
    return {
        "saved": True,
        "fields": list_fields(root, python),
        "message": f"已保存 {len(payload)} 项设置，重启{names}后生效。",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["list", "save"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--python", default=None, help="加载配置所用的 Python 解释器")
    args = parser.parse_args()
    root = args.root.resolve()
    python = find_python(root, args.python)
    try:
        if args.action == "list":
            print(json.dumps({"fields": list_fields(root, python)}, ensure_ascii=False))
        else:
            payload = json.loads(sys.stdin.read() or "{}")
            print(json.dumps(save(root, python, payload), ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
