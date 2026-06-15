# coding: utf-8
"""
ErrorBus — 客户端统一状态出口（CLI 阶段实现）。

职责：
  - 线程安全地维护一份运行状态快照
  - 状态变化时 + 每 5s 心跳时原子写入 status.json
  - 退出时删除 status.json
  - 向 macOS 发系统通知（同一类型 30s 内去重）
    · 优先用现代 UserNotifications 框架（UNUserNotificationCenter），
      通知以 CapsWriter 自身身份与图标发送；
    · 框架不可用或进程无 bundle 身份（脱离 .app 裸跑）时回退 osascript，
      此时图标会被系统归属给“脚本编辑器”（卷轴），但通知内容不受影响。

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
import uuid
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

        # 原生通知中心（UNUserNotificationCenter）；None 表示不可用，notify 将回退 osascript
        self._un_center: Any = None
        self._init_native_notifier()

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

        self._deliver(message)

    # ------------------------------------------------------------------
    # 通知投递（优先原生 UN，失败回退 osascript）
    # ------------------------------------------------------------------

    def _init_native_notifier(self) -> None:
        """尝试初始化现代 UserNotifications 框架（UNUserNotificationCenter）。

        成功条件：pyobjc 的 UserNotifications 可导入，且当前进程具备有效 bundle
        身份（即运行在 CapsWriter.app 内）。满足时通知以 CapsWriter 自身身份 +
        图标发送；任何失败都静默降级（self._un_center 保持 None），由 _deliver()
        回退到 osascript，确保通知绝不丢失。

        授权：此处请求一次通知授权，首次启动会弹一次系统询问；结果由系统持久化，
        跨启动有效，不会重复打扰。
        """
        try:
            # 先确认进程具备有效 bundle 身份。脱离 .app 裸跑时 bundleIdentifier 为 None，
            # 此时 currentNotificationCenter() 会在 dispatch_once 块内抛 ObjC 异常并直接
            # abort（try/except 拦不住），故必须在调用前拦截，直接降级 osascript。
            from Foundation import NSBundle
            if not NSBundle.mainBundle().bundleIdentifier():
                self._un_center = None
                return

            from UserNotifications import (
                UNUserNotificationCenter,
                UNAuthorizationOptionAlert,
                UNAuthorizationOptionSound,
            )
            center = UNUserNotificationCenter.currentNotificationCenter()
            # 请求 横幅 + 声音 授权；completion 回调结果此处无需处理
            center.requestAuthorizationWithOptions_completionHandler_(
                UNAuthorizationOptionAlert | UNAuthorizationOptionSound,
                lambda granted, error: None,
            )
            self._un_center = center
        except Exception:
            self._un_center = None

    def _deliver(self, message: str) -> None:
        """投递一条通知：优先原生 UN（带 app 图标），失败回退 osascript。"""
        if self._un_center is not None and self._deliver_native(message):
            self._notify_dbg(f"UN 投递: {message}")
            return
        # 回退：osascript display notification（异步，不阻塞调用线程）
        self._notify_dbg(f"osascript 回退投递: {message}（_un_center={self._un_center!r}）")
        subprocess.Popen([
            'osascript', '-e',
            f'display notification "{message}" with title "CapsWriter"',
        ])

    @staticmethod
    def _notify_dbg(msg: str) -> None:
        """通知链路诊断日志：追加写入 ~/.capswriter/logs/notify.log，便于确证实际走的路径。"""
        try:
            log = Path.home() / '.capswriter' / 'logs' / 'notify.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open('a', encoding='utf-8') as fp:
                fp.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")
        except Exception:
            pass

    def _deliver_native(self, message: str) -> bool:
        """通过 UNUserNotificationCenter 投递；成功返回 True，否则 False（触发回退）。"""
        try:
            from UserNotifications import (
                UNMutableNotificationContent,
                UNNotificationRequest,
            )
            content = UNMutableNotificationContent.alloc().init()
            content.setTitle_("CapsWriter")
            content.setBody_(message)
            # identifier 用随机 uuid，避免相同 id 互相覆盖
            request = UNNotificationRequest.requestWithIdentifier_content_trigger_(
                uuid.uuid4().hex, content, None,
            )
            self._un_center.addNotificationRequest_withCompletionHandler_(
                request, lambda error: None,
            )
            return True
        except Exception:
            return False

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
