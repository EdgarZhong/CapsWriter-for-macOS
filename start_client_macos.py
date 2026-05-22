#!/usr/bin/env python3
# coding: utf-8
"""
CapsWriter macOS .app bundle 客户端入口。

职责：
  1. 在主线程初始化 NSApplication，赋予进程 macOS GUI 应用身份。
     这样 macOS 麦克风隐私指示器（菜单栏左侧橙色胶囊）会显示
     "CapsWriter" 而不是 "Python3"。
  2. 写入客户端 PID 文件，供 capswriterd 读取以发送 SIGTERM。
  3. 在子线程运行 CapsWriterClient（asyncio 事件循环）。
  4. 主线程运行 NSApplication RunLoop，保持 GUI 应用身份存活。

进程模型：
  主线程  → NSApplication.run()（RunLoop，提供 macOS 应用身份）
  子线程  → CapsWriterClient.start()（asyncio 事件循环，录音 / WebSocket / 结果处理）

信号处理：
  SIGTERM → 调用 client.stop()（恢复 remap 等清理），移除 PID 文件，退出
  SIGINT  → 双击确认退出（保持原有行为）
"""
from __future__ import annotations

import atexit
import os
import signal
import sys
import threading
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# 项目根目录（本文件位于项目根）
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# PID 文件（供 capswriterd 读取）
# ---------------------------------------------------------------------------
STATE_DIR = Path.home() / '.capswriter' / 'state'
CLIENT_PID_FILE = STATE_DIR / 'client.pid'


def _write_client_pid() -> None:
    """写入当前进程 PID，供 capswriterd 追踪。"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CLIENT_PID_FILE.write_text(str(os.getpid()))


def _clear_client_pid() -> None:
    """清理 PID 文件。"""
    try:
        CLIENT_PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# NSApplication 初始化（主线程）
# ---------------------------------------------------------------------------

from AppKit import NSApplication, NSApplicationActivationPolicyAccessory  # noqa: E402
from Foundation import NSObject  # noqa: E402

# 创建 NSApplication 单例，设置为 Accessory 策略（无 Dock 图标、无应用菜单栏）
_nsapp = NSApplication.sharedApplication()
_nsapp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)


def _start_client_thread() -> None:
    """启动 CapsWriterClient 子线程。"""
    t = threading.Thread(target=_run_client, name="CapsWriterClientThread", daemon=True)
    t.start()


class _AppDelegate(NSObject):
    """NSApplication 代理，处理应用生命周期事件。"""

    def applicationDidFinishLaunching_(self, notification):
        """启动完成后：先通过 AVFoundation 确保麦克风权限已处理，再启动客户端。
        PortAudio/sounddevice 直接调 CoreAudio，不会触发 TCC 弹窗；
        必须走 AVFoundation API 才能让 macOS 弹出授权对话框。
        """
        try:
            import AVFoundation as _avf
            status = _avf.AVCaptureDevice.authorizationStatusForMediaType_(
                _avf.AVMediaTypeAudio
            )
            # AVAuthorizationStatusNotDetermined = 0：尚未询问，弹窗请求
            if status == 0:
                def _on_mic_permission(granted):
                    if not granted:
                        print("[CapsWriter.app] 麦克风权限被用户拒绝", file=sys.stderr)
                    _start_client_thread()
                _avf.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
                    _avf.AVMediaTypeAudio, _on_mic_permission
                )
                return  # 等待用户响应后由回调启动客户端
        except Exception as e:
            print(f"[CapsWriter.app] AVFoundation 权限请求失败，直接启动: {e}", file=sys.stderr)

        # 权限已确定（授权/拒绝/受限）或 AVFoundation 不可用，直接启动
        _start_client_thread()

    def applicationWillTerminate_(self, notification):
        """NSApplication 即将退出时的清理回调。"""
        _cleanup()


# ---------------------------------------------------------------------------
# 客户端引用（跨线程共享）
# ---------------------------------------------------------------------------
_client = None
_client_lock = threading.Lock()


def _cleanup() -> None:
    """统一清理：停止客户端 + 移除 PID 文件。"""
    global _client
    with _client_lock:
        if _client is not None:
            try:
                _client.stop()
            except Exception:
                pass
            _client = None
    _clear_client_pid()


# ---------------------------------------------------------------------------
# 信号处理（必须在主线程注册）
# ---------------------------------------------------------------------------

_last_sigint_time = 0.0


def _on_sigterm(signum, frame):
    """SIGTERM：立即清理并退出（capswriterd / launchd 停止场景）。"""
    _cleanup()
    sys.exit(0)


def _on_sigint(signum, frame):
    """SIGINT：双击确认退出（交互场景，保持原有行为）。"""
    global _last_sigint_time
    now = time.time()
    if now - _last_sigint_time > 1.0:
        _last_sigint_time = now
        print(f"\n收到 {signal.Signals(signum).name}，1秒内再次按下将会退出...")
    else:
        print(f"\n收到 {signal.Signals(signum).name}，确认退出...\n")
        _cleanup()
        sys.exit(0)


signal.signal(signal.SIGTERM, _on_sigterm)
signal.signal(signal.SIGINT, _on_sigint)


# ---------------------------------------------------------------------------
# 客户端子线程
# ---------------------------------------------------------------------------

def _run_client() -> None:
    """在子线程运行 CapsWriterClient。"""
    global _client
    try:
        from core.client.app import CapsWriterClient
        client = CapsWriterClient()
        with _client_lock:
            _client = client
        # register_signals=False：信号已在主线程处理，子线程不可调用 signal.signal()
        client.start(register_signals=False)
    except Exception as e:
        print(f"[CapsWriter.app] 客户端异常退出: {e}", file=sys.stderr)
    finally:
        # 客户端退出后，通知 NSApplication 终止
        _nsapp.performSelectorOnMainThread_withObject_waitUntilDone_(
            'terminate:', None, False
        )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main() -> int:
    # 写入 PID 文件
    _write_client_pid()
    atexit.register(_clear_client_pid)

    # 设置 NSApplication 代理
    delegate = _AppDelegate.alloc().init()
    _nsapp.setDelegate_(delegate)

    # 客户端线程由 applicationDidFinishLaunching_ 在确认麦克风权限后启动

    # 主线程运行 NSApplication RunLoop（阻塞）
    from PyObjCTools import AppHelper
    AppHelper.runEventLoop()

    # RunLoop 退出后清理
    _cleanup()
    return 0


if __name__ == '__main__':
    sys.exit(main())
