# coding: utf-8
"""
macOS 权限工具箱探针（诊断用）。

目的：把"四象限工具箱"里每个与权限表交互的原语，在**当前进程身份**下各调一遍，
打印真实返回值，用来钉死几个我们目前只能推测、必须实测确认的行为，尤其是：
  - cdhash 变化(stale)时 IOHIDCheckAccess 到底返回 Unknown 还是 Granted；
  - CGEventTapCreate 在缺权限时返回什么、是否会把条目写进输入监控表。

重要：TCC 的"责任进程"是真正跑这段代码的进程。
  - 从终端跑 → 量到的是终端/当前进程的权限，**不是 CapsWriter.app 的**；
  - 要量 CapsWriter 自身，请把本脚本用 CapsWriter.app 的同一签名身份启动。
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import subprocess
import sys


def _hr(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


# ------------------------------------------------------------------
# 运行上下文（量的是哪个进程的 TCC）
# ------------------------------------------------------------------
def dump_context() -> None:
    _hr("运行上下文（决定量的是谁的 TCC）")
    print(f"pid              = {os.getpid()}")
    print(f"sys.executable   = {sys.executable}")
    exe = sys.executable
    # 责任进程通常是 bundle 主可执行文件；这里把解释器路径打出来供判断
    try:
        bid = None
        from Foundation import NSBundle  # type: ignore
        mb = NSBundle.mainBundle()
        bid = mb.bundleIdentifier() if mb is not None else None
        print(f"mainBundle id    = {bid}")
        print(f"mainBundle path  = {mb.bundlePath() if mb is not None else None}")
    except Exception as e:
        print(f"mainBundle       = <读取失败: {e}>")
    # 当前可执行文件的签名/cdhash（codesign 看的是文件，不一定等于责任进程，仅作参考）
    try:
        out = subprocess.run(
            ["codesign", "-dvvv", exe],
            capture_output=True, text=True, timeout=10,
        )
        for line in (out.stderr or "").splitlines():
            if any(k in line for k in ("Identifier=", "Signature=", "CDHash=", "TeamIdentifier=", "flags=")):
                print(f"codesign         | {line}")
    except Exception as e:
        print(f"codesign         = <失败: {e}>")


# ------------------------------------------------------------------
# 象限①：辅助功能 × 探测
# ------------------------------------------------------------------
def probe_ax() -> None:
    _hr("象限① 辅助功能 × 探测  AXIsProcessTrusted()")
    try:
        from ApplicationServices import AXIsProcessTrusted
        val = bool(AXIsProcessTrusted())
        print(f"AXIsProcessTrusted() = {val}   (True=信任且生效 / False=无法区分无条目|关|stale)")
    except Exception as e:
        print(f"<失败: {e}>")


# ------------------------------------------------------------------
# 象限③：输入监控 × 探测
# ------------------------------------------------------------------
_IOHID_LISTEN_EVENT = 1
_HID_NAMES = {0: "Granted(0)", 1: "Denied(1)", 2: "Unknown(2)"}


def _load_iokit():
    iokit = ctypes.CDLL(ctypes.util.find_library("IOKit"))
    iokit.IOHIDCheckAccess.restype = ctypes.c_int
    iokit.IOHIDCheckAccess.argtypes = [ctypes.c_uint]
    iokit.IOHIDRequestAccess.restype = ctypes.c_bool
    iokit.IOHIDRequestAccess.argtypes = [ctypes.c_uint]
    return iokit


def probe_im() -> None:
    _hr("象限③ 输入监控 × 探测  IOHIDCheckAccess(ListenEvent)")
    try:
        iokit = _load_iokit()
        val = int(iokit.IOHIDCheckAccess(_IOHID_LISTEN_EVENT))
        print(f"IOHIDCheckAccess() = {_HID_NAMES.get(val, val)}")
        print("  ★ stale 关键：若界面上明明有条目却返回 Unknown(2) → stale 伪装成『无条目』")
        print("              若返回 Granted(0) 但下面 tap 起不来 → stale 伪装成『已授权』")
    except Exception as e:
        print(f"<失败: {e}>")


# ------------------------------------------------------------------
# 象限④：输入监控 × 操作（tap 创建 = 唯一写表动作 + 裁决原语 tap_alive）
# ------------------------------------------------------------------
def register_im_via_tap_and_check() -> None:
    _hr("象限④ 输入监控 × 操作  CGEventTapCreate 尝试（写表 + tap_alive 裁决）")
    try:
        import Quartz

        mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
        )

        def _cb(proxy, type_, event, refcon):
            return event

        tap = Quartz.CGEventTapCreate(
            Quartz.kCGHIDEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,
            mask,
            _cb,
            None,
        )
        if tap is None:
            print("CGEventTapCreate() = None  → tap 创建失败（多为缺权限）")
            print("  ★ 关键观察：此调用后去『输入监控』面板看，CapsWriter 条目有没有被写进去")
            return
        enabled_before = bool(Quartz.CGEventTapIsEnabled(tap))
        Quartz.CGEventTapEnable(tap, True)
        enabled_after = bool(Quartz.CGEventTapIsEnabled(tap))
        print(f"CGEventTapCreate() = <非 None，创建成功>")
        print(f"CGEventTapIsEnabled 创建后 = {enabled_before}")
        print(f"CGEventTapIsEnabled enable 后 = {enabled_after}  ← tap_alive 权威判据")
        # 立即关掉，避免影响系统键盘
        Quartz.CGEventTapEnable(tap, False)
    except Exception as e:
        print(f"<失败: {e}>")


def main() -> None:
    print("macOS 权限工具箱探针 —— 量的是【当前进程】的 TCC，注意责任进程归属")
    dump_context()
    probe_ax()
    probe_im()
    register_im_via_tap_and_check()
    _hr("完成")
    print("解读提示：")
    print("  - 从终端跑 = 终端/解释器的权限，不代表 CapsWriter.app；")
    print("  - 要量 CapsWriter，请用 .app 身份启动本脚本后再对照各值。")


if __name__ == "__main__":
    main()
