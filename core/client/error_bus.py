# coding: utf-8
"""
ErrorBus — 客户端统一状态出口（CLI 阶段实现）。

职责：
  - 线程安全地维护一份运行状态快照
  - 状态变化时 + 每 5s 心跳时原子写入 status.json
  - 退出时删除 status.json
  - 向 macOS 发系统通知（同一类型 30s 内去重）

当前阶段仅实现 CLI 所需的 status.json + 通知；
GUI 阶段再扩展 Unix socket 推送和菜单栏状态更新。

status.json 路径：~/.capswriter/state/status.json
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any


# status.json 写入路径
_STATUS_FILE = Path.home() / '.capswriter' / 'state' / 'status.json'

# 同一类通知的最短发送间隔（秒）
_NOTIF_DEDUP_SECS = 30.0


class ErrorBus:
    """客户端统一状态出口（CLI 阶段）。

    线程安全：update() / heartbeat() / notify() 可从任意线程调用。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {
            "pid":              os.getpid(),
            "state":            "starting",   # starting|connecting|ready|recording|error
            "server_connected": False,
            "accessibility_ok": False,
            "microphone_ok":    False,
            "started_at":       datetime.now().isoformat(timespec='seconds'),
            "last_heartbeat":   None,
            "last_error":       None,
            "last_error_at":    None,
        }
        # 通知去重：key → 上次发送时间戳
        self._notif_last: dict[str, float] = {}

        # 确保目录存在并写入初始状态
        _STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._write()

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def update(self, **fields: Any) -> None:
        """更新一个或多个状态字段，有变化时立即写入 status.json。"""
        changed = False
        with self._lock:
            for k, v in fields.items():
                if self._state.get(k) != v:
                    self._state[k] = v
                    changed = True
        if changed:
            self._write()

    def heartbeat(self) -> None:
        """刷新 last_heartbeat 时间戳，由外部每 5s 调用一次。"""
        with self._lock:
            self._state["last_heartbeat"] = datetime.now().isoformat(timespec='seconds')
        self._write()

    def notify(self, message: str, key: str) -> None:
        """发 macOS 系统通知，同一 key 在 30s 内只发一次。

        Args:
            message: 通知正文。
            key: 去重 key（如 "server_connected"、"server_disconnected"）。
        """
        now = time.monotonic()
        with self._lock:
            last = self._notif_last.get(key, 0.0)
            if now - last < _NOTIF_DEDUP_SECS:
                return
            self._notif_last[key] = now

        # 异步发送，不阻塞调用线程
        subprocess.Popen([
            'osascript', '-e',
            f'display notification "{message}" with title "CapsWriter"',
        ])

    def cleanup(self) -> None:
        """退出时删除 status.json（确保 capswriter status 不显示过期数据）。"""
        try:
            _STATUS_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 内部写入（原子操作：写 tmp → rename）
    # ------------------------------------------------------------------

    def _write(self) -> None:
        """原子写入 status.json，线程安全。"""
        with self._lock:
            data = dict(self._state)
            data["last_heartbeat"] = datetime.now().isoformat(timespec='seconds')

        tmp = _STATUS_FILE.with_suffix('.json.tmp')
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
            tmp.replace(_STATUS_FILE)
        except Exception:
            pass
