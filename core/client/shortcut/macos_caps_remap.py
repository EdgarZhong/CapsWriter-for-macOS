# coding: utf-8
"""
macOS Caps Lock 运行期 remap 管理器。

设计目标：
1. 客户端运行时，把物理 `Caps Lock` 临时映射成 `F18`；
2. 客户端退出时恢复用户原有 `UserKeyMapping`；
3. 即使用户本来就配置了其它 remap，也尽量保留并恢复它们。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import logger


CAPS_LOCK_HID = 0x700000039
F18_HID = 0x70000006D
STATE_DIR = Path.home() / ".capswriter"
STATE_FILE = STATE_DIR / "original_user_key_mapping.json"


class MacOSCapsRemapError(RuntimeError):
    """`hidutil` 调用或映射恢复失败时抛出的异常。"""


def _run_hidutil(args: list[str]) -> str:
    """
    调用 `hidutil` 并返回标准输出。

    `hidutil` 的返回格式不是 JSON，而是苹果风格的旧式属性文本，
    因此这里把原始文本完整保留下来，后续再做结构化解析。
    """
    proc = subprocess.run(
        ["hidutil", *args],
        check=False,
        capture_output=True,
        text=True,
    )

    if proc.returncode != 0:
        raise MacOSCapsRemapError(
            f"hidutil failed rc={proc.returncode}, stdout={proc.stdout!r}, stderr={proc.stderr!r}"
        )

    return proc.stdout.strip()


def _parse_mapping_value(value: str) -> int:
    """把十进制或十六进制字符串解析成整数。"""
    return int(value.strip(), 0)


def parse_user_key_mapping_raw(raw: str) -> list[dict[str, int]]:
    """
    把 `hidutil property --get UserKeyMapping` 的文本结果解析成结构化列表。

    典型输出有三种情况：
    1. `()`：表示当前没有任何 remap；
    2. `(null)`：某些系统版本上的空结果；
    3. `({ ...; ...; })`：包含一条或多条映射字典。
    """
    stripped = raw.strip()
    if stripped in {"", "()", "(null)", "null"}:
        return []

    mappings: list[dict[str, int]] = []
    for block in re.finditer(r"\{([^}]*)\}", stripped, flags=re.S):
        item: dict[str, int] = {}
        for key, value in re.findall(r"([A-Za-z0-9_]+)\s*=\s*([^;]+);", block.group(1)):
            item[key] = _parse_mapping_value(value)
        if item:
            mappings.append(item)

    return mappings


def get_user_key_mapping_raw() -> str:
    """读取当前系统 `UserKeyMapping` 的原始文本。"""
    return _run_hidutil(["property", "--get", "UserKeyMapping"])


def get_user_key_mapping() -> list[dict[str, int]]:
    """读取当前系统 `UserKeyMapping` 的结构化结果。"""
    return parse_user_key_mapping_raw(get_user_key_mapping_raw())


def set_user_key_mapping(mapping: list[dict[str, int]]) -> None:
    """把结构化映射列表写回系统。"""
    payload = json.dumps({"UserKeyMapping": mapping}, separators=(",", ":"))
    _run_hidutil(["property", "--set", payload])


def build_caps_to_f18_mapping(existing_mapping: list[dict[str, int]]) -> list[dict[str, int]]:
    """
    在保留用户其它 remap 的前提下，覆盖掉 `Caps Lock` 的源映射。

    如果用户本来就把 `Caps Lock` remap 到其它键，这里会在运行期临时替换成 `F18`，
    退出时再恢复原始值。
    """
    filtered = [
        item
        for item in existing_mapping
        if int(item.get("HIDKeyboardModifierMappingSrc", -1)) != CAPS_LOCK_HID
    ]
    filtered.append(
        {
            "HIDKeyboardModifierMappingSrc": CAPS_LOCK_HID,
            "HIDKeyboardModifierMappingDst": F18_HID,
        }
    )
    return filtered


def _persist_original_mapping(mapping: list[dict[str, int]]) -> None:
    """把原始映射写入状态文件，便于异常退出后手工恢复。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps({"UserKeyMapping": mapping}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_persisted_original_mapping() -> list[dict[str, int]] | None:
    """从状态文件加载上一次保存的原始映射。"""
    if not STATE_FILE.exists():
        return None

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        raise MacOSCapsRemapError(f"failed to read persisted mapping: {exc}") from exc

    mapping = data.get("UserKeyMapping")
    if not isinstance(mapping, list):
        return None
    return mapping


@dataclass
class MacOSCapsRemapSession:
    """一次客户端运行期对应的一段 remap 生命周期。"""

    original_mapping: list[dict[str, int]] = field(default_factory=list)
    enabled_mapping: list[dict[str, int]] = field(default_factory=list)
    enabled: bool = False

    def start(self) -> None:
        """保存原始映射并启用 `Caps Lock -> F18`。"""
        self.original_mapping = get_user_key_mapping()
        logger.info("[caps-remap] original UserKeyMapping=%s", self.original_mapping)
        _persist_original_mapping(self.original_mapping)

        self.enabled_mapping = build_caps_to_f18_mapping(self.original_mapping)
        logger.info("[caps-remap] enabling CapsLock -> F18")
        set_user_key_mapping(self.enabled_mapping)
        self.enabled = True

    def restore(self) -> None:
        """恢复到启动前的原始映射。"""
        if not self.enabled:
            return

        logger.info("[caps-remap] restoring original UserKeyMapping=%s", self.original_mapping)
        set_user_key_mapping(self.original_mapping)
        self.enabled = False


def restore_from_persisted_state() -> None:
    """按状态文件恢复用户原始映射。"""
    original_mapping = _load_persisted_original_mapping()
    if original_mapping is None:
        raise MacOSCapsRemapError("no persisted original mapping found")

    logger.info("[caps-remap] restoring from persisted state file")
    set_user_key_mapping(original_mapping)


def clear_user_key_mapping() -> None:
    """手工清空所有映射。仅作为救援命令使用。"""
    logger.warning("[caps-remap] clearing UserKeyMapping")
    set_user_key_mapping([])


def _build_parser() -> argparse.ArgumentParser:
    """构建命令行解析器。"""
    parser = argparse.ArgumentParser(description="macOS Caps Lock remap helper")
    parser.add_argument(
        "action",
        choices=["status", "enable", "restore", "clear"],
        help="status: 查看状态；enable: 启用 Caps->F18；restore: 恢复原始映射；clear: 清空全部映射",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    args = _build_parser().parse_args(argv)

    if args.action == "status":
        raw = get_user_key_mapping_raw()
        parsed = parse_user_key_mapping_raw(raw)
        print(f"raw={raw}")
        print(f"parsed={parsed}")
        print(f"state_file={STATE_FILE}")
        return 0

    if args.action == "enable":
        session = MacOSCapsRemapSession()
        session.start()
        print("enabled")
        return 0

    if args.action == "restore":
        restore_from_persisted_state()
        print("restored")
        return 0

    if args.action == "clear":
        clear_user_key_mapping()
        print("cleared")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
