# coding: utf-8
"""
macOS 权限渐进引导（仅辅助功能 Accessibility）。

背景与设计见 docs/macos-architecture-decisions.md 第六节。

为什么要这一层（而不是一段写死的弹窗文案）：
1. 当前阶段的产品口径已经收敛为：把「辅助功能」作为**唯一需要显式引导用户处理**的权限。
   用户首次安装或运行期撤权后，只要把这一项处理好，再重启客户端即可重新尝试接管键盘；
   不再把「输入监控」作为首次引导或恢复引导的一部分，避免把用户带到一个条目出现时机不稳定、
   且与 active tap 实时可用性并不稳定等价的页面。
2. 用户肉眼**无法区分**「关着的有效条目」和「关着的失效条目（dev 重新签名后
   cdhash 对不上的旧记录）」——列表长一样、没时间戳。让用户自己判断「该删还是该拨」
   是不现实的。

渐进探测式策略（用户任意时刻只面对一条明确指令，由 app 替他判断 stale）：
- 只引导「辅助功能」这一项，granted 直接跳过；
- unknown（从没问过）→ 触发系统**原生授权弹窗**；
- denied（有记录但关着）→ 打开面板、提示「把开关打开」，后台**轮询**：
  · 轮询到生效 → 完成；
  · 超时仍不生效 → 判定为「旧记录」→ 升级提示「− 删除 → 重启 → 重新允许」。
"""

from __future__ import annotations

import ctypes
import ctypes.util
import subprocess
import time
from collections.abc import Callable

from . import logger

# ---- 权限面板 URL ----
_PANE_ACCESSIBILITY = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"

# ---- IOHIDCheckAccess / IOHIDRequestAccess（输入监控，IOKit C 符号）----
# kIOHIDRequestTypeListenEvent = 1（监听类，对应键盘事件监听）
_IOHID_LISTEN_EVENT = 1
# IOHIDCheckAccess 返回 kIOHIDAccessType：0=Granted 1=Denied 2=Unknown
HID_GRANTED, HID_DENIED, HID_UNKNOWN = 0, 1, 2

# 轮询节奏：用户在系统设置里拨开关后，多久内能被检测到
_POLL_INTERVAL_S = 1.0
_PROMPT_POLL_TIMEOUT_S = 8.0    # 触发原生弹窗后等待用户响应的时长
_TOGGLE_POLL_TIMEOUT_S = 25.0   # 引导拨开关后等待生效的时长（超时即判定旧记录）


def _load_iokit():
    """绑定 IOKit 的 IOHIDCheckAccess / IOHIDRequestAccess（PyObjC 不直接暴露，手动 ctypes 绑）。"""
    try:
        iokit = ctypes.CDLL(ctypes.util.find_library("IOKit"))
        iokit.IOHIDCheckAccess.restype = ctypes.c_int
        iokit.IOHIDCheckAccess.argtypes = [ctypes.c_uint]
        iokit.IOHIDRequestAccess.restype = ctypes.c_bool
        iokit.IOHIDRequestAccess.argtypes = [ctypes.c_uint]
        return iokit
    except Exception as e:  # 绑定失败时输入监控相关探测降级为「不阻塞」
        logger.warning("[perm-guide] 无法绑定 IOKit IOHID 符号: %s", e)
        return None


_iokit = _load_iokit()


# ------------------------------------------------------------------
# 探测层（只读，不弹窗、不改任何状态）
# ------------------------------------------------------------------

def check_accessibility() -> bool:
    """辅助功能是否已授权（AXIsProcessTrusted，只读无弹窗）。"""
    try:
        from ApplicationServices import AXIsProcessTrusted
        return bool(AXIsProcessTrusted())
    except Exception as e:
        logger.warning("[perm-guide] AXIsProcessTrusted 失败: %s", e)
        return True  # 探测本身失败时不误报「无权限」，避免误导用户去删条目


def check_input_monitoring() -> int:
    """输入监控状态：HID_GRANTED / HID_DENIED / HID_UNKNOWN。"""
    if _iokit is None:
        return HID_GRANTED  # 无法探测则不阻塞
    try:
        return int(_iokit.IOHIDCheckAccess(_IOHID_LISTEN_EVENT))
    except Exception as e:
        logger.warning("[perm-guide] IOHIDCheckAccess 失败: %s", e)
        return HID_GRANTED


# ------------------------------------------------------------------
# 原生授权弹窗（仅 unknown / 首次场景；有记录但被拒时这些调用会静默 no-op）
# ------------------------------------------------------------------

def prompt_accessibility() -> None:
    """触发系统「辅助功能」原生授权弹窗（仅在无授权决定记录时显示）。"""
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )
        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
    except Exception as e:
        logger.warning("[perm-guide] 触发辅助功能弹窗失败: %s", e)


def request_input_monitoring() -> None:
    """触发系统「输入监控」原生授权弹窗（仅在 unknown 时显示）。"""
    if _iokit is None:
        return
    try:
        _iokit.IOHIDRequestAccess(_IOHID_LISTEN_EVENT)
    except Exception as e:
        logger.warning("[perm-guide] 触发输入监控弹窗失败: %s", e)


# ------------------------------------------------------------------
# 默认 IO（可被 bridge 用 ErrorBus 注入替换）
# ------------------------------------------------------------------

def _open_pane(url: str) -> None:
    subprocess.Popen(['open', url])


def _default_notify(msg: str) -> None:
    logger.info("[perm-guide] %s", msg)


def _default_dialog(body: str, title: str) -> None:
    # 注意：body 内只用「」全角引号，不要用 ASCII 双引号，避免破坏 AppleScript 字符串
    script = f'display dialog "{body}" with title "{title}" buttons {{"好的"}} default button "好的"'
    subprocess.Popen(['osascript', '-e', script])


# ------------------------------------------------------------------
# 辅助功能权限的渐进引导
# ------------------------------------------------------------------

def _poll_accessibility_granted(timeout: float) -> bool:
    """在 timeout 内轮询辅助功能是否变为已授权。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check_accessibility():
            return True
        time.sleep(_POLL_INTERVAL_S)
    return False


def run_guide(
    notify: Callable[[str], None] | None = None,
    dialog: Callable[[str, str], None] | None = None,
) -> str:
    """仅引导「辅助功能」权限。

    设计意图：
    - 首次冷启动和运行期 fatal 都会复用本入口；
    - 当前阶段只处理辅助功能，不再自动把用户路由到输入监控页面；
    - 输入监控相关现象保留给用户手动排障，不纳入程序内的正式权限恢复路径。

    返回：
    - 'all_granted'：辅助功能已就绪（仍需重启 CapsWriter 重新接管键盘）
    - 'need_restart'：需要用户删除失效旧记录后重启
    """
    notify = notify or _default_notify
    dialog = dialog or _default_dialog

    label = '辅助功能'

    if check_accessibility():
        notify("✅ 权限已就绪，请重启 CapsWriter 以重新接管键盘")
        return 'all_granted'

    # 1) 原生弹窗：覆盖「从没问过 / 首次运行」——会按当前签名新建有效记录，最干净
    prompt_accessibility()
    if _poll_accessibility_granted(_PROMPT_POLL_TIMEOUT_S):
        notify(f"✅ {label} 权限已就绪")
        notify("✅ 权限已就绪，请重启 CapsWriter 以重新接管键盘")
        return 'all_granted'

    # 2) 有记录但关着：打开面板，提示「拨开关」，后台轮询（先试最轻的动作）
    _open_pane(_PANE_ACCESSIBILITY)
    notify(f"请在系统设置「{label}」中打开 CapsWriter 的开关，打开后会自动检测，无需点任何按钮")
    if _poll_accessibility_granted(_TOGGLE_POLL_TIMEOUT_S):
        notify(f"✅ {label} 权限已就绪")
        notify("✅ 权限已就绪，请重启 CapsWriter 以重新接管键盘")
        return 'all_granted'

    # 3) 拨了仍不生效 = 旧记录（多见于 dev 重新签名后 cdhash 对不上）：升级到删除+重启
    dialog(
        f"CapsWriter 的「{label}」权限没有生效。\n\n"
        f"这通常是更新后旧授权记录失效导致。请在已打开的设置里：\n"
        f"选中 CapsWriter，点「−」把它删除，\n"
        f"然后重启 CapsWriter，按弹窗重新允许即可。",
        "CapsWriter 需要重新授权",
    )
    return 'need_restart'
