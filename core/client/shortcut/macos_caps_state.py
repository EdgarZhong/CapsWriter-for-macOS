# coding: utf-8
"""
macOS Caps Lock 状态管理器（IOKit 实现）。

通过 IOKit 的 IOHIDSystem 接口直接读写 Caps Lock 锁定状态。

为什么不能用 CGEventPost 合成 Caps Lock 事件？
  合成的键盘事件（CGEventPost）不经过 HID 硬件层，不会触发
  IOHIDSystem 内部的 Caps Lock 状态机，因此无法改变锁定状态和 LED。

为什么不需要 Accessibility 权限？
  IOHIDSetModifierLockState 是 IOKit 参数接口，不需要辅助功能授权。
"""

from __future__ import annotations

import ctypes
from . import logger

# ---------------------------------------------------------------------------
# IOKit 函数加载（懒加载，只在第一次调用时初始化）
# ---------------------------------------------------------------------------

_IOKit: ctypes.CDLL | None = None


def _get_iokit() -> ctypes.CDLL:
    global _IOKit
    if _IOKit is not None:
        return _IOKit

    lib = ctypes.CDLL('/System/Library/Frameworks/IOKit.framework/IOKit')

    # IOServiceGetMatchingService(mach_port_t masterPort, CFDictionaryRef matching) -> io_service_t
    lib.IOServiceGetMatchingService.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    lib.IOServiceGetMatchingService.restype = ctypes.c_uint

    # IOServiceMatching(const char *name) -> CFMutableDictionaryRef
    lib.IOServiceMatching.argtypes = [ctypes.c_char_p]
    lib.IOServiceMatching.restype = ctypes.c_void_p

    # IOServiceOpen(io_service_t, task_port_t, uint32_t type, io_connect_t*) -> kern_return_t
    lib.IOServiceOpen.argtypes = [
        ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)
    ]
    lib.IOServiceOpen.restype = ctypes.c_int

    # IOServiceClose(io_connect_t) -> kern_return_t
    lib.IOServiceClose.argtypes = [ctypes.c_uint]
    lib.IOServiceClose.restype = ctypes.c_int

    # IOObjectRelease(io_object_t) -> kern_return_t
    lib.IOObjectRelease.argtypes = [ctypes.c_uint]
    lib.IOObjectRelease.restype = ctypes.c_int

    # IOHIDGetModifierLockState(io_connect_t, int selector, bool* state) -> kern_return_t
    lib.IOHIDGetModifierLockState.argtypes = [
        ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_bool)
    ]
    lib.IOHIDGetModifierLockState.restype = ctypes.c_int

    # IOHIDSetModifierLockState(io_connect_t, int selector, bool state) -> kern_return_t
    lib.IOHIDSetModifierLockState.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_bool]
    lib.IOHIDSetModifierLockState.restype = ctypes.c_int

    _IOKit = lib
    return lib


def _get_mach_task_self() -> int:
    """获取当前进程的 mach task port。"""
    libsystem = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
    libsystem.mach_task_self.restype = ctypes.c_uint
    return libsystem.mach_task_self()


# IOHIDSystem 连接类型：kIOHIDParamConnectType = 1（参数接口，无需 root）
_kIOHIDParamConnectType = 1

# IOHIDGetModifierLockState / IOHIDSetModifierLockState 的 selector
# 来自 IOHIDParameter.h：kIOHIDCapsLockState = 0x00000001, kIOHIDNumLockState = 0x00000002
_kIOHIDCapsLockState = 0x00000001


def _open_hid_connect() -> int:
    """
    打开 IOHIDSystem 参数连接，返回 io_connect_t。

    失败时返回 0。调用方负责在使用后调用 IOServiceClose。
    """
    iokit = _get_iokit()

    service = iokit.IOServiceGetMatchingService(0, iokit.IOServiceMatching(b"IOHIDSystem"))
    if not service:
        logger.warning("[caps-state] IOHIDSystem service not found")
        return 0

    connect = ctypes.c_uint(0)
    kr = iokit.IOServiceOpen(service, _get_mach_task_self(), _kIOHIDParamConnectType, ctypes.byref(connect))
    iokit.IOObjectRelease(service)

    if kr != 0:
        logger.warning("[caps-state] IOServiceOpen failed: kr=0x%x", kr)
        return 0

    return connect.value


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def get_caps_lock_state() -> bool:
    """读取当前 Caps Lock 锁定状态。"""
    iokit = _get_iokit()
    connect = _open_hid_connect()
    if not connect:
        return False

    try:
        state = ctypes.c_bool(False)
        kr = iokit.IOHIDGetModifierLockState(connect, _kIOHIDCapsLockState, ctypes.byref(state))
        if kr != 0:
            logger.warning("[caps-state] IOHIDGetModifierLockState failed: kr=0x%x", kr)
            return False
        return bool(state.value)
    finally:
        iokit.IOServiceClose(connect)


def set_caps_lock_state(enabled: bool) -> bool:
    """直接设置 Caps Lock 锁定状态。返回是否成功。"""
    iokit = _get_iokit()
    connect = _open_hid_connect()
    if not connect:
        return False

    try:
        kr = iokit.IOHIDSetModifierLockState(connect, _kIOHIDCapsLockState, ctypes.c_bool(enabled))
        if kr != 0:
            logger.warning("[caps-state] IOHIDSetModifierLockState failed: kr=0x%x", kr)
            return False
        logger.debug("[caps-state] CapsLock set to %s", enabled)
        return True
    finally:
        iokit.IOServiceClose(connect)


def toggle_caps_lock_state() -> None:
    """
    通过 IOKit 直接切换 Caps Lock 锁定状态。

    不走键盘事件管道，不受 hidutil remap 影响，不需要 Accessibility 权限。
    """
    iokit = _get_iokit()
    connect = _open_hid_connect()
    if not connect:
        logger.warning("[caps-state] cannot toggle CapsLock: failed to open IOHIDSystem")
        return

    try:
        state = ctypes.c_bool(False)
        kr = iokit.IOHIDGetModifierLockState(connect, _kIOHIDCapsLockState, ctypes.byref(state))
        if kr != 0:
            logger.warning("[caps-state] IOHIDGetModifierLockState failed: kr=0x%x", kr)
            return

        new_state = not bool(state.value)
        kr = iokit.IOHIDSetModifierLockState(connect, _kIOHIDCapsLockState, ctypes.c_bool(new_state))
        if kr != 0:
            logger.warning("[caps-state] IOHIDSetModifierLockState failed: kr=0x%x", kr)
            return

        logger.info("[caps-state] CapsLock toggled: %s -> %s", state.value, new_state)
    finally:
        iokit.IOServiceClose(connect)
