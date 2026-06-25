# coding: utf-8
"""
macOS Caps Lock 运行期 remap 管理器。

设计目标：
1. 客户端运行时，把物理 `Caps Lock` 临时映射成 `F18`；
2. 客户端退出时恢复用户原有 `UserKeyMapping`；
3. 即使用户本来就配置了其它 remap，也尽量保留并恢复它们。

注意：本模块设计为可独立运行（python -m core.client.shortcut.macos_caps_remap），
不依赖 core.client 的日志链，使用标准库 logging 作为默认 logger；
当在客户端进程内使用时，会自动接入 client logger（两者共享同一 logger 对象名 'client'）。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# 使用命名 logger 'client'，与项目 setup_logger('client') 共享同一对象。
# 独立 CLI 时由 main() 的 setup_logger 初始化；库模式时由 core/client/__init__.py 初始化。
logger = logging.getLogger('client')


CAPS_LOCK_HID = 0x700000039
F18_HID = 0x70000006D

# 状态文件目录和路径（spec 要求 state/ 子目录）
STATE_DIR = Path.home() / ".capswriter" / "state"
STATE_FILE = STATE_DIR / "original_user_key_mapping.json"

# 当前已知的 Caps->F18 remap 特征值，用于检测脏状态
_CAPS_TO_F18_ENTRY = {
    "HIDKeyboardModifierMappingSrc": CAPS_LOCK_HID,
    "HIDKeyboardModifierMappingDst": F18_HID,
}

# 状态文件 schema 版本，便于后续兼容升级
_SCHEMA_VERSION = 1


class MacOSCapsRemapError(RuntimeError):
    """`hidutil` 调用或映射恢复失败时抛出的异常。"""


def _run_hidutil(args: list[str]) -> str:
    """调用 `/usr/bin/hidutil` 并返回标准输出。"""
    cmd = ["/usr/bin/hidutil", *args]
    logger.debug("hidutil cmd: %s", cmd)
    proc = subprocess.run(
        cmd,
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
    logger.debug("set UserKeyMapping payload: %s", payload)
    _run_hidutil(["property", "--set", payload])


def _mapping_contains_caps_to_f18(mapping: list[dict[str, int]]) -> bool:
    """检查映射列表中是否已含有 Caps->F18 条目（用于检测脏状态）。"""
    for entry in mapping:
        if (
            int(entry.get("HIDKeyboardModifierMappingSrc", -1)) == CAPS_LOCK_HID
            and int(entry.get("HIDKeyboardModifierMappingDst", -1)) == F18_HID
        ):
            return True
    return False


def build_caps_to_f18_mapping(existing_mapping: list[dict[str, int]]) -> list[dict[str, int]]:
    """
    在保留用户其它 remap 的前提下，覆盖掉 `Caps Lock` 的源映射。
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


# ──────────────────────────────────────────
# 状态文件读写（完整 schema + atomic write）
# ──────────────────────────────────────────

def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _persist_state(
    original_mapping: list[dict[str, int]],
    enabled_mapping: list[dict[str, int]],
    client_pid: int | None,
    active: bool,
) -> None:
    """
    把完整状态原子写入状态文件。

    atomic write：先写 .tmp 临时文件，再 rename，避免崩溃时留下半截 JSON。
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "schema_version": _SCHEMA_VERSION,
        "owner": "CapsWriter",
        "purpose": "macos_caps_remap_restore_snapshot",
        "created_at": _now_iso(),
        "client_pid": client_pid,
        "active": active,
        "original_user_key_mapping": original_mapping,
        "enabled_user_key_mapping": enabled_mapping,
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2)

    # 写临时文件再 rename 保证原子性
    tmp_fd, tmp_path = tempfile.mkstemp(dir=STATE_DIR, prefix=".tmp_state_", suffix=".json")
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, STATE_FILE)
        logger.debug("[caps-remap] state file written: active=%s pid=%s", active, client_pid)
    except Exception:
        # 写失败时尝试清理临时文件
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _load_state() -> dict | None:
    """
    加载状态文件，返回完整 dict；文件不存在或损坏时返回 None。

    同时兼容旧格式（只有 {"UserKeyMapping": [...]}）。
    """
    if not STATE_FILE.exists():
        return None

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[caps-remap] failed to read state file: %s", exc)
        return None

    # 兼容旧格式：{"UserKeyMapping": [...]}
    if "original_user_key_mapping" not in data and "UserKeyMapping" in data:
        return {
            "schema_version": 0,
            "active": False,
            "client_pid": None,
            "original_user_key_mapping": data["UserKeyMapping"],
            "enabled_user_key_mapping": [],
        }

    return data


def _load_original_mapping() -> list[dict[str, int]] | None:
    """从状态文件提取 original_user_key_mapping 字段。"""
    state = _load_state()
    if state is None:
        return None
    mapping = state.get("original_user_key_mapping")
    if not isinstance(mapping, list):
        return None
    return mapping


def _is_pid_running(pid: int) -> bool:
    """检查指定 PID 是否仍在运行。"""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # 进程存在但我们没有权限发信号（仍算运行中）
        return True


def _check_client_not_running() -> None:
    """
    检查 client 是否未在运行；若仍在运行则抛出异常。

    根据状态文件的 active 字段和 client_pid 判断。
    """
    state = _load_state()
    if state is None:
        return  # 无状态文件，不阻塞

    if not state.get("active", False):
        return  # 上次已正常退出

    pid = state.get("client_pid")
    if pid is not None and _is_pid_running(pid):
        raise MacOSCapsRemapError(
            f"CapsWriter client is running (pid={pid}). "
            "Please stop it first: capswriter stop"
        )

    # active=True 但 PID 已不存在，说明上次崩溃退出，允许继续
    logger.warning("[caps-remap] state says active but pid=%s not found, proceeding", pid)


# ──────────────────────────────────────────
# Remap 会话
# ──────────────────────────────────────────

@dataclass
class MacOSCapsRemapSession:
    """一次客户端运行期对应的一段 remap 生命周期。"""

    original_mapping: list[dict[str, int]] = field(default_factory=list)
    enabled_mapping: list[dict[str, int]] = field(default_factory=list)
    enabled: bool = False
    _client_pid: int | None = field(default=None, init=False, repr=False)

    def start(self) -> None:
        """
        保存原始映射并启用 `Caps Lock -> F18`。

        写入顺序：先持久化 original snapshot，再写入系统 remap，
        保证崩溃时状态文件里存的仍是干净的 original。
        """
        current = get_user_key_mapping()

        if _mapping_contains_caps_to_f18(current):
            # 系统上已有 Caps->F18，说明上轮客户端崩溃后未恢复。
            # 优先从状态文件里找上一次存下的干净 original。
            persisted = _load_original_mapping()
            if persisted is not None and not _mapping_contains_caps_to_f18(persisted):
                logger.warning("[caps-remap] stale Caps->F18 detected, using persisted original=%s", persisted)
                self.original_mapping = persisted
            else:
                clean = [e for e in current if int(e.get("HIDKeyboardModifierMappingSrc", -1)) != CAPS_LOCK_HID]
                logger.warning("[caps-remap] no clean persisted state, using clean=%s", clean)
                self.original_mapping = clean
        else:
            self.original_mapping = current

        self._client_pid = os.getpid()
        logger.info("[caps-remap] original UserKeyMapping=%s pid=%s", self.original_mapping, self._client_pid)

        self.enabled_mapping = build_caps_to_f18_mapping(self.original_mapping)

        # 先持久化 snapshot，再写系统 remap（崩溃时仍可从文件恢复）
        _persist_state(
            original_mapping=self.original_mapping,
            enabled_mapping=self.enabled_mapping,
            client_pid=self._client_pid,
            active=True,
        )

        logger.info("[caps-remap] enabling CapsLock -> F18")
        set_user_key_mapping(self.enabled_mapping)
        self.enabled = True

    def update_child_pid(self, child_pid: int) -> None:
        """
        supervisor 拉起子进程后，把子进程 PID 补写进状态文件。

        这样 `remap restore` 可以额外检查子进程是否仍在运行。
        """
        state = _load_state()
        if state is None:
            return

        # 同时记录 supervisor pid（client_pid）和实际 client 子进程 pid
        state["child_pid"] = child_pid
        content = json.dumps(state, ensure_ascii=False, indent=2)

        tmp_fd, tmp_path = tempfile.mkstemp(dir=STATE_DIR, prefix=".tmp_state_", suffix=".json")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, STATE_FILE)
            logger.debug("[caps-remap] state updated child_pid=%s", child_pid)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def restore(self) -> None:
        """恢复到启动前的原始映射，并在状态文件中标记为非活跃。

        双实例保护：remap 是系统全局状态，只有当前持有者才能 restore。
        若另一个实例已接管（state 文件 client_pid != 自己），跳过 restore，
        避免清掉新实例的 remap 导致键盘接管断掉。
        """
        if not self.enabled:
            return

        # 双实例保护：检查 remap 归属
        try:
            state = _load_state()
            if state is not None and state.get("client_pid") != self._client_pid:
                logger.warning(
                    "[caps-remap] 跳过 restore：remap 已被 pid=%s 接管（自身 pid=%s）",
                    state.get("client_pid"), self._client_pid,
                )
                self.enabled = False
                return
        except Exception:
            pass  # state 读取失败时保守 restore

        logger.info("[caps-remap] restoring original UserKeyMapping=%s", self.original_mapping)
        set_user_key_mapping(self.original_mapping)
        self.enabled = False

        # 标记状态文件为非活跃，让 restore CLI 命令知道可以安全执行
        try:
            _persist_state(
                original_mapping=self.original_mapping,
                enabled_mapping=self.enabled_mapping,
                client_pid=self._client_pid,
                active=False,
            )
        except Exception as exc:
            logger.warning("[caps-remap] failed to update state file on restore: %s", exc)


def restore_from_persisted_state() -> None:
    """
    按状态文件恢复用户原始映射。

    只能在 client 未运行时使用；client 正在运行时会拒绝并提示。
    """
    _check_client_not_running()

    original_mapping = _load_original_mapping()
    if original_mapping is None:
        raise MacOSCapsRemapError("no persisted original mapping found")

    logger.info("[caps-remap] restoring from persisted state file: %s", original_mapping)
    set_user_key_mapping(original_mapping)

    # 更新状态文件，标记为非活跃
    state = _load_state()
    if state is not None:
        state["active"] = False
        content = json.dumps(state, ensure_ascii=False, indent=2)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=STATE_DIR, prefix=".tmp_state_", suffix=".json")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, STATE_FILE)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def clear_user_key_mapping() -> None:
    """手工清空所有映射。仅作为救援命令使用，必须带 --force 参数。"""
    set_user_key_mapping([])


# ──────────────────────────────────────────
# 独立 CLI
# ──────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    """构建命令行解析器。"""
    parser = argparse.ArgumentParser(description="macOS Caps Lock remap helper")
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("status", help="查看当前系统 UserKeyMapping 和状态文件")
    sub.add_parser("enable", help="启用 Caps->F18 remap")
    sub.add_parser("restore", help="按状态文件恢复原始映射（client 未运行时可用）")

    clear_p = sub.add_parser("clear", help="清空全部 UserKeyMapping（救援命令）")
    clear_p.add_argument("--force", action="store_true", help="必须带此参数才能执行清空")

    return parser


def main(argv: list[str] | None = None) -> int:
    """命令行入口。"""
    # 独立 CLI 时初始化日志，与客户端共用同一 logger 名 'client'
    from core.logger import setup_logger
    setup_logger('client', level='DEBUG')

    args = _build_parser().parse_args(argv)

    if args.action == "status":
        raw = get_user_key_mapping_raw()
        parsed = parse_user_key_mapping_raw(raw)
        print(f"当前系统 UserKeyMapping:")
        print(f"  raw   = {raw}")
        print(f"  parsed= {parsed}")
        print(f"  含 Caps->F18: {_mapping_contains_caps_to_f18(parsed)}")
        print(f"\n状态文件: {STATE_FILE}  (exists={STATE_FILE.exists()})")
        if STATE_FILE.exists():
            state = _load_state()
            if state:
                print(f"  schema_version = {state.get('schema_version')}")
                print(f"  active         = {state.get('active')}")
                print(f"  client_pid     = {state.get('client_pid')}")
                print(f"  child_pid      = {state.get('child_pid')}")
                print(f"  created_at     = {state.get('created_at')}")
                print(f"  original_mapping = {state.get('original_user_key_mapping')}")
        return 0

    if args.action == "enable":
        session = MacOSCapsRemapSession()
        session.start()
        print("enabled: Caps Lock -> F18")
        return 0

    if args.action == "restore":
        try:
            restore_from_persisted_state()
            print("restored: 原始 UserKeyMapping 已恢复")
        except MacOSCapsRemapError as e:
            print(f"错误: {e}")
            return 1
        return 0

    if args.action == "clear":
        if not args.force:
            print("错误: clear 是危险救援命令，必须带 --force 参数")
            print("用法: python -m core.client.shortcut.macos_caps_remap clear --force")
            return 1
        try:
            _check_client_not_running()
        except MacOSCapsRemapError as e:
            print(f"错误: {e}")
            return 1
        clear_user_key_mapping()
        print("cleared: UserKeyMapping=[]")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
