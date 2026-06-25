# coding: utf-8
"""
macOS 权限引导（辅助功能 Accessibility + 输入监控 Input Monitoring）。

完整设计见 docs/macos-architecture-decisions.md 第六节。本模块承载两样东西：

1. **四象限工具箱**：{辅助功能, 输入监控} × {探测, 操作} 的单一职责接口；
2. **引导状态机** `run_guide()`：引导用户开启两项权限后重启。

—— 核心思想 ——
只为「干净首装」自动注册条目、引导用户开开关。
引导流程永远只说「打开开关」和「请重启」，**绝不说「删条目」**——
stale / 疑难杂症统一交给 `capswriter reset-permissions` + 文档。

—— 关键认知 ——
- IM 条目注册**不能靠** `IOHIDRequestAccess`（实测无效）；唯一可靠手段是
  **AX 就绪后尝试创建一次 CGEventTap**（listener 执行，本模块通过 `try_register_im` 触发）。
"""

from __future__ import annotations

import ctypes
import ctypes.util
import enum
import subprocess
import time
from collections.abc import Callable

from . import logger

# ==================================================================
# 全局时效状态（喂给 ErrorBus → status.json → 菜单栏圆点 → CLI）
# ==================================================================


class PermPhase(enum.Enum):
    """权限引导状态机的当前节点 —— 即「我们此刻处在哪种时效状态」的唯一标识。

    这个枚举是状态机的**输出**（由实时探测 + 心跳推导而来），不是一个能与真实
    权限各自漂移的独立变量；任何时候都应以 run_guide 推导出的值为准。
    """

    PROBING = "probing"            # 启动 / 重检中，尚未定论
    GUIDE_AX = "guide_ax"          # 缺辅助功能，已引导用户去开开关（待开 + 重启）
    GUIDE_IM = "guide_im"          # 缺输入监控，已引导用户去开开关（待开 + 重启）
    READY = "ready"                # 两权限齐 + tap 真活（唯一允许工作的态）
    REVOKED = "revoked"            # 运行中掉权，已恢复 remap 放行键盘（由 bridge 用）


# ==================================================================
# 常量
# ==================================================================

# ---- 权限面板 URL（系统设置是单窗口，第二条只是把同一窗口导航过去）----
_PANE_ACCESSIBILITY = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
_PANE_INPUT_MONITORING = "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"

# ---- IOHIDCheckAccess / IOHIDRequestAccess（输入监控，IOKit C 符号）----
# 注：外部二次核查确认 IOHIDCheckAccess/IOHIDRequestAccess 是**有官方文档**的符号
# （并非未公开），故保留使用；但 IOHIDCheckAccess 仅作**提示性探测**，真理归 tap 心跳。
# kIOHIDRequestTypeListenEvent = 1（监听类，对应键盘事件监听）
_IOHID_LISTEN_EVENT = 1
# IOHIDCheckAccess 返回 kIOHIDAccessType：0=Granted 1=Denied 2=Unknown
HID_GRANTED, HID_DENIED, HID_UNKNOWN = 0, 1, 2

# 轮询节奏：用户在系统设置里拨开关后，多久内能被检测到
_POLL_INTERVAL_S = 1.0
_PROMPT_POLL_TIMEOUT_S = 8.0    # 触发原生弹窗后等待用户响应的时长
_TOGGLE_POLL_TIMEOUT_S = 25.0   # 引导拨开关后等待生效的时长（超时即通知重启 / reset-permissions）



# ==================================================================
# IOKit 绑定（输入监控探测/请求，PyObjC 不直接暴露，手动 ctypes 绑）
# ==================================================================


def _load_iokit():
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


# ==================================================================
# 四象限工具箱：{辅助功能, 输入监控} × {探测, 操作}
# 每个函数单一职责，可被状态机独立编排，也便于单测。
# ==================================================================

# ------------------------------------------------------------------
# 象限① 辅助功能 × 探测
# ------------------------------------------------------------------
def check_accessibility() -> bool:
    """辅助功能是否已授权（AXIsProcessTrusted，只读、无弹窗、无副作用）。

    探测失败时返回 True（不误报「无权限」），避免误导用户去删条目。
    注意 GPT 核查 C1：它只回 bool，分不出「无条目 / 关 / stale」——这不要紧，
    stale 由 tap 心跳兜底，本探测不承担区分 stale 的职责。
    """
    try:
        from ApplicationServices import AXIsProcessTrusted
        return bool(AXIsProcessTrusted())
    except Exception as e:
        logger.warning("[perm-guide] AXIsProcessTrusted 失败: %s", e)
        return True


# ------------------------------------------------------------------
# 象限② 辅助功能 × 操作（注册条目 + 请求授权）
# ------------------------------------------------------------------
def prompt_accessibility() -> None:
    """触发系统「辅助功能」原生授权弹窗，并把本 app 注册进辅助功能列表。

    这是唯一能「注册 AX 条目 + 弹原生框」的官方手段（GPT 核查 C2）。
    仅在「从没决定过」时才会真正弹框；已有记录时静默 no-op。
    """
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )
        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
    except Exception as e:
        logger.warning("[perm-guide] 触发辅助功能弹窗失败: %s", e)


# ------------------------------------------------------------------
# 象限③ 输入监控 × 探测（仅作提示，真理归 tap 心跳）
# ------------------------------------------------------------------
def check_input_monitoring() -> int:
    """输入监控状态：HID_GRANTED / HID_DENIED / HID_UNKNOWN。

    仅作**提示性探测**：用来决定「该不该把用户导到 IM 面板」。
    它的 3 态比 CGPreflightListenEventAccess 的 bool 多一条信息
    （Unknown≈条目还没注册、Denied≈条目在但没开），适合做文案分支。
    但 GPT 核查 C9：stale 下它返回什么「无定论」，故**不作真理**。
    """
    if _iokit is None:
        return HID_GRANTED  # 无法探测则不阻塞
    try:
        return int(_iokit.IOHIDCheckAccess(_IOHID_LISTEN_EVENT))
    except Exception as e:
        logger.warning("[perm-guide] IOHIDCheckAccess 失败: %s", e)
        return HID_GRANTED


def input_monitoring_granted() -> bool:
    """输入监控是否已就绪（3 态收敛成 bool，供状态机判断）。"""
    return check_input_monitoring() == HID_GRANTED


# ------------------------------------------------------------------
# 象限④ 输入监控 × 操作（让条目出现）
# ------------------------------------------------------------------
# 真正的载力注册手段是「在 AX 就绪前提下尝试创建一次 CGEventTap」，那段逻辑在
# listener 里，本模块通过 run_guide 的 try_register_im 回调触发。这里只保留一个
# **降级为无害试探**的 IOHIDRequestAccess：实测它注册不了 IM 条目，但调一下无害，
# 仅作 belt-and-suspenders，绝不作为注册的依赖。
def request_input_monitoring_nudge() -> None:
    """（非载力）顺手戳一下 IOHIDRequestAccess —— 实测注册不了 IM 条目，不可依赖。"""
    if _iokit is None:
        return
    try:
        _iokit.IOHIDRequestAccess(_IOHID_LISTEN_EVENT)
    except Exception as e:
        logger.warning("[perm-guide] IOHIDRequestAccess 试探失败: %s", e)


# ------------------------------------------------------------------
# 唤起面板（GUI 操作）
# ------------------------------------------------------------------
def open_accessibility_pane() -> None:
    """打开系统设置「辅助功能」面板。"""
    subprocess.Popen(['open', _PANE_ACCESSIBILITY])


def open_input_monitoring_pane() -> None:
    """打开系统设置「输入监控」面板。"""
    subprocess.Popen(['open', _PANE_INPUT_MONITORING])


# ==================================================================
# 默认 IO（可被 bridge 用 ErrorBus 注入替换）
# ==================================================================
def _default_notify(msg: str) -> None:
    logger.info("[perm-guide] %s", msg)


# ==================================================================
# 轮询小工具
# ==================================================================
def _poll(predicate: Callable[[], bool], timeout: float) -> bool:
    """在 timeout 内轮询 predicate，True 即返回；超时返回 False。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(_POLL_INTERVAL_S)
    return False


# ==================================================================
# 引导状态机
# ==================================================================
def run_guide(
    notify: Callable[[str], None] | None = None,
    *,
    try_register_im: Callable[[], None] | None = None,
    on_phase: Callable[[PermPhase], None] | None = None,
) -> PermPhase:
    """跑一遍权限引导，返回最终落到的 `PermPhase`。

    注入回调（由 bridge 接 listener 提供；缺省时降级，便于单测/裸跑）：
    - `try_register_im()`：让 listener 尝试创建一次 tap，副作用是把 IM 条目注册进列表
      （前提 AX 已就绪）。缺省 = 顺手戳一下无害的 IOHIDRequestAccess。
    - `on_phase(phase)`：上报全局时效状态给 ErrorBus。

    流程：辅助功能先行 → AX 就绪后注册 IM 条目 → 引导 IM → 权限齐了通知重启。
    引导永远只说「打开开关」和「请重启」，**绝不说「删条目」**；
    stale / 疑难杂症由 `capswriter reset-permissions` 兜底。
    """
    notify = notify or _default_notify
    register_im = try_register_im or request_input_monitoring_nudge

    def _set_phase(phase: PermPhase) -> PermPhase:
        if on_phase is not None:
            try:
                on_phase(phase)
            except Exception as e:
                logger.warning("[perm-guide] on_phase 回调异常: %s", e)
        return phase

    _set_phase(PermPhase.PROBING)

    # ============== 阶段 1：辅助功能先行 ==============
    if not check_accessibility():
        _set_phase(PermPhase.GUIDE_AX)
        # 1a) 原生弹窗：覆盖「从没问过 / 首次运行」，按当前签名新建有效记录
        prompt_accessibility()
        if not _poll(check_accessibility, _PROMPT_POLL_TIMEOUT_S):
            # 1b) 有记录但关着 / 弹窗未响应：打开面板、提示「拨开关」
            open_accessibility_pane()
            notify("请在系统设置「辅助功能」里打开 CapsWriter 的开关，打开后会自动检测")
            if not _poll(check_accessibility, _TOGGLE_POLL_TIMEOUT_S):
                # 1c) 超时仍未生效 → 通知重启（可能需要 reset-permissions）
                notify("辅助功能仍未生效，请重启 CapsWriter 重试；若反复不成功，"
                       "请运行 capswriter reset-permissions 后重新启动")
                return _set_phase(PermPhase.GUIDE_AX)
        notify("✅ 辅助功能已就绪")

    # ============== 阶段 2：AX 已就绪 → 补一次 tap 尝试，注册 IM 条目 ==============
    request_input_monitoring_nudge()  # 无害试探
    try:
        register_im()
    except Exception as e:
        logger.warning("[perm-guide] try_register_im 回调异常: %s", e)

    # ============== 阶段 3：输入监控 ==============
    if not input_monitoring_granted():
        _set_phase(PermPhase.GUIDE_IM)
        open_input_monitoring_pane()
        # 注意：IM 条目何时出现在列表里**不可靠**（实测时有时无，CGEventTap 注册
        # 的副作用并不总能即时让条目出现）。故文案不再断言「条目已就位」，而是显式
        # 兜底：列表里没有就让用户手动点「+」搜索软件名添加。
        notify(
            "请在系统设置「输入监控」里打开 CapsWriter 的开关；"
            "若列表里没有 CapsWriter，请点击「+」号，搜索并添加 CapsWriter，"
            "然后重启 CapsWriter"
        )
        return _set_phase(PermPhase.GUIDE_IM)

    # ============== 阶段 4：权限齐了 → 提示重启 ==============
    notify("权限已就绪，请重启 CapsWriter 以启用键盘接管")
    return _set_phase(PermPhase.GUIDE_IM)
