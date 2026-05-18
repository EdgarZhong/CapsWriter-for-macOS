# coding: utf-8
"""
macOS Caps Lock 原生事件探针

这个脚本用于同时观察两条不同层级的输入链路：
1. Quartz Event Tap：更接近当前客户端正在使用的 `CGEventTap` 路线。
2. IOHIDManager：更接近底层物理 HID 设备输入值，便于判断是否能拿到稳定的
   `Caps Lock` press/release 语义。

当前排查目标不是“证明某条路线一定可行”，而是把以下问题拆开看清楚：
1. `CGEventTap` 有没有真的拦住系统大小写切换。
2. `IOHIDManager` 能不能稳定看到 `Caps Lock` 的原始值变化。
3. 两条链路在同一时刻各自看到了什么。

典型用法：
    .venv/bin/python -m core.tools.macos_caps_probe --tap hid --mode observe --duration 20
    .venv/bin/python -m core.tools.macos_caps_probe --tap manager --mode observe --duration 20
    .venv/bin/python -m core.tools.macos_caps_probe --tap hid-manager --mode swallow-caps --duration 20
"""

from __future__ import annotations

import argparse
import ctypes
import platform
import sys
import time
from dataclasses import dataclass

import Quartz
from CoreFoundation import (
    CFMachPortCreateRunLoopSource,
    CFRunLoopAddSource,
    CFRunLoopGetCurrent,
    CFRunLoopRunInMode,
    kCFRunLoopDefaultMode,
)


# Apple 官方文档与历史 QA 都把 57 作为 `Caps Lock` 的虚拟键码。
CAPS_LOCK_KEYCODE = 57

# USB HID Usage Tables 中，键盘页是 0x07，Caps Lock 的 usage 是 0x39。
KEYBOARD_USAGE_PAGE = 0x07
CAPS_LOCK_USAGE = 0x39

# 当前排查中最有价值的三类键盘事件。
EVENT_NAME_MAP = {
    Quartz.kCGEventKeyDown: 'keyDown',
    Quartz.kCGEventKeyUp: 'keyUp',
    Quartz.kCGEventFlagsChanged: 'flagsChanged',
}

# 只关心键盘相关事件，避免探针噪声过大。
DEFAULT_EVENT_MASK = (
    Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
    | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
    | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
)


@dataclass(slots=True)
class ProbeConfig:
    """探针运行参数。"""

    tap: str
    mode: str
    duration: float
    keycode: int


class IOHIDManagerListener:
    """
    IOHIDManager 输入值监听器。

    这里不尝试拦截系统行为，只负责观察更靠近物理设备层的原始输入值。
    如果它能稳定看到 `Caps Lock` 的 value 从 0/1 来回切换，就说明至少
    “更底层物理事件可见” 这件事有继续工程化的价值。
    """

    def __init__(self, start_time: float, target_usage: int):
        self.start_time = start_time
        self.target_usage = target_usage
        self._manager = None
        self._callback = None

        self._iokit = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/IOKit.framework/IOKit')
        self._cf = ctypes.cdll.LoadLibrary('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')

        self._prepare_symbols()

    def _prepare_symbols(self) -> None:
        """配置本次会用到的 C 函数签名。"""
        iokit = self._iokit
        cf = self._cf

        self._c_void_p = ctypes.c_void_p
        self._c_uint32 = ctypes.c_uint32
        self._c_int32 = ctypes.c_int32
        self._c_long = ctypes.c_long
        self._c_double = ctypes.c_double
        self._c_ubyte = ctypes.c_ubyte

        iokit.IOHIDManagerCreate.argtypes = [self._c_void_p, self._c_uint32]
        iokit.IOHIDManagerCreate.restype = self._c_void_p

        iokit.IOHIDManagerOpen.argtypes = [self._c_void_p, self._c_uint32]
        iokit.IOHIDManagerOpen.restype = self._c_int32

        iokit.IOHIDManagerClose.argtypes = [self._c_void_p, self._c_uint32]
        iokit.IOHIDManagerClose.restype = self._c_int32

        iokit.IOHIDManagerScheduleWithRunLoop.argtypes = [
            self._c_void_p,
            self._c_void_p,
            self._c_void_p,
        ]
        iokit.IOHIDManagerScheduleWithRunLoop.restype = None

        self._callback_type = ctypes.CFUNCTYPE(
            None,
            self._c_void_p,
            self._c_int32,
            self._c_void_p,
            self._c_void_p,
        )
        iokit.IOHIDManagerRegisterInputValueCallback.argtypes = [
            self._c_void_p,
            self._callback_type,
            self._c_void_p,
        ]
        iokit.IOHIDManagerRegisterInputValueCallback.restype = None

        iokit.IOHIDValueGetElement.argtypes = [self._c_void_p]
        iokit.IOHIDValueGetElement.restype = self._c_void_p

        iokit.IOHIDValueGetIntegerValue.argtypes = [self._c_void_p]
        iokit.IOHIDValueGetIntegerValue.restype = self._c_long

        iokit.IOHIDElementGetUsagePage.argtypes = [self._c_void_p]
        iokit.IOHIDElementGetUsagePage.restype = self._c_uint32

        iokit.IOHIDElementGetUsage.argtypes = [self._c_void_p]
        iokit.IOHIDElementGetUsage.restype = self._c_uint32

        cf.CFRunLoopGetCurrent.argtypes = []
        cf.CFRunLoopGetCurrent.restype = self._c_void_p

        cf.CFRunLoopRunInMode.argtypes = [
            self._c_void_p,
            self._c_double,
            self._c_ubyte,
        ]
        cf.CFRunLoopRunInMode.restype = self._c_int32

    def start(self) -> bool:
        """启动 IOHIDManager 监听。"""
        self._manager = self._iokit.IOHIDManagerCreate(None, 0)
        if not self._manager:
            print('创建 IOHIDManager 失败。', flush=True)
            return False

        def callback(context, result, sender, value):
            element = self._iokit.IOHIDValueGetElement(value)
            usage_page = int(self._iokit.IOHIDElementGetUsagePage(element))
            usage = int(self._iokit.IOHIDElementGetUsage(element))
            integer_value = int(self._iokit.IOHIDValueGetIntegerValue(value))

            # 只关心键盘页，避免鼠标、消费类设备等输入把日志淹没。
            if usage_page != KEYBOARD_USAGE_PAGE:
                return

            print(
                _format_iohid_line(
                    start_time=self.start_time,
                    usage_page=usage_page,
                    usage=usage,
                    integer_value=integer_value,
                    target_usage=self.target_usage,
                ),
                flush=True,
            )

        self._callback = self._callback_type(callback)

        run_loop = self._cf.CFRunLoopGetCurrent()
        run_loop_mode = ctypes.c_void_p.in_dll(self._cf, 'kCFRunLoopDefaultMode')

        self._iokit.IOHIDManagerRegisterInputValueCallback(
            self._manager,
            self._callback,
            None,
        )
        self._iokit.IOHIDManagerScheduleWithRunLoop(
            self._manager,
            run_loop,
            run_loop_mode,
        )
        open_result = int(self._iokit.IOHIDManagerOpen(self._manager, 0))
        print(f'IOHIDManager OPEN: result={open_result}', flush=True)
        return open_result == 0

    def stop(self) -> None:
        """关闭 IOHIDManager。"""
        if self._manager:
            close_result = int(self._iokit.IOHIDManagerClose(self._manager, 0))
            print(f'IOHIDManager CLOSE: result={close_result}', flush=True)
            self._manager = None


def _build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(description='macOS Caps Lock 原生事件探针')
    parser.add_argument(
        '--tap',
        choices=('session', 'hid', 'manager', 'hid-manager'),
        default='hid',
        help=(
            '选择监听层级：session/hid 使用 Quartz Event Tap，'
            'manager 使用 IOHIDManager，hid-manager 同时打开两者'
        ),
    )
    parser.add_argument(
        '--mode',
        choices=('observe', 'swallow-caps'),
        default='observe',
        help='observe=只读观察；swallow-caps=仅对 Event Tap 吞掉 Caps Lock 事件',
    )
    parser.add_argument(
        '--duration',
        type=float,
        default=20.0,
        help='监听时长（秒）',
    )
    parser.add_argument(
        '--keycode',
        type=int,
        default=CAPS_LOCK_KEYCODE,
        help='需要重点跟踪的目标键码，默认 57 即 Caps Lock',
    )
    return parser


def _parse_args(argv: list[str]) -> ProbeConfig:
    """把命令行参数转换成强类型配置。"""
    args = _build_parser().parse_args(argv)
    return ProbeConfig(
        tap=args.tap,
        mode=args.mode,
        duration=args.duration,
        keycode=args.keycode,
    )


def _tap_location(name: str) -> int:
    """把友好的 tap 名称映射为 Quartz 常量。"""
    if name == 'session':
        return Quartz.kCGSessionEventTap
    if name == 'hid':
        return Quartz.kCGHIDEventTap
    raise ValueError(f'未知 tap 名称: {name}')


def _read_hid_flags() -> tuple[bool, int]:
    """
    读取当前 HID 层的全局修饰键状态。

    这里刻意读取“全局当前状态”，而不是单个事件包里的 flags。
    这样可以对比“事件声称将要切换成什么状态”和“系统最终是否真的切换过去”。
    """
    flags = int(Quartz.CGEventSourceFlagsState(Quartz.kCGEventSourceStateHIDSystemState))
    alpha_on = bool(flags & Quartz.kCGEventFlagMaskAlphaShift)
    return alpha_on, flags


def _format_event_line(start_time: float, event_type: int, event, target_keycode: int) -> str:
    """
    生成一行易读的 Event Tap 日志。

    输出中同时保留：
    - 事件类型
    - 当前事件携带的 keycode 与 flags
    - 当前 HID 全局 flags
    """
    elapsed = time.time() - start_time
    keycode = int(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode))
    event_flags = int(Quartz.CGEventGetFlags(event))
    source_pid = int(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUnixProcessID))
    hid_alpha, hid_flags = _read_hid_flags()
    marker = ' <TARGET>' if keycode == target_keycode else ''
    event_name = EVENT_NAME_MAP.get(event_type, str(event_type))
    return (
        f'{elapsed:7.3f}s '
        f'[event_tap] '
        f'type={event_name:12} '
        f'keycode={keycode:3d} '
        f'event_flags=0x{event_flags:08x} '
        f'hid_alpha={hid_alpha} '
        f'hid_flags=0x{hid_flags:08x} '
        f'pid={source_pid}{marker}'
    )


def _format_iohid_line(
    start_time: float,
    usage_page: int,
    usage: int,
    integer_value: int,
    target_usage: int,
) -> str:
    """
    生成一行易读的 IOHIDManager 日志。

    如果 `Caps Lock` 在这一层确实表现得像普通键，那么理论上这里应该能看到更接近
    `press/release` 的 value 变化，而不是只有 `flagsChanged` 风格的翻转结果。
    """
    elapsed = time.time() - start_time
    hid_alpha, hid_flags = _read_hid_flags()
    marker = ' <TARGET>' if usage == target_usage else ''
    semantic = ''
    if usage == target_usage:
        semantic = ' press' if integer_value else ' release'
    return (
        f'{elapsed:7.3f}s '
        f'[iohid_mgr] '
        f'usage_page=0x{usage_page:02x} '
        f'usage=0x{usage:02x} '
        f'value={integer_value} '
        f'hid_alpha={hid_alpha} '
        f'hid_flags=0x{hid_flags:08x}'
        f'{semantic}{marker}'
    )


def main(argv: list[str] | None = None) -> int:
    """脚本入口。"""
    argv = list(sys.argv[1:] if argv is None else argv)
    config = _parse_args(argv)

    if platform.system() != 'Darwin':
        print('该探针只支持 macOS / Darwin。')
        return 2

    start_time = time.time()
    tap = None
    event_tap_callback = None
    manager_listener = None

    use_event_tap = config.tap in ('session', 'hid', 'hid-manager')
    use_iohid_manager = config.tap in ('manager', 'hid-manager')

    if use_event_tap:
        tap_name = 'hid' if config.tap == 'hid-manager' else config.tap

        def callback(proxy, event_type, event, refcon):
            """
            Quartz Event Tap 回调。

            这里必须保持逻辑尽量轻量，只做日志打印和极小的条件分支，
            避免因为回调阻塞过久导致 tap 被系统自动禁用。
            """
            print(_format_event_line(start_time, event_type, event, config.keycode), flush=True)

            # `swallow-caps` 模式只吞目标键码的事件，其它键保持原样放行，
            # 便于把“是否能拦住 Caps Lock”和“其它键仍正常流动”这两件事分开观察。
            if config.mode == 'swallow-caps':
                keycode = int(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode))
                if event_type == Quartz.kCGEventFlagsChanged and keycode == config.keycode:
                    print('          -> swallow target flagsChanged', flush=True)
                    return None

            return event

        event_tap_callback = callback
        tap = Quartz.CGEventTapCreate(
            _tap_location(tap_name),
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,
            DEFAULT_EVENT_MASK,
            event_tap_callback,
            None,
        )
        if not tap:
            print(f'创建 {tap_name} tap 失败。')
            return 1

        loop_source = CFMachPortCreateRunLoopSource(None, tap, 0)
        run_loop = CFRunLoopGetCurrent()
        CFRunLoopAddSource(run_loop, loop_source, kCFRunLoopDefaultMode)
        Quartz.CGEventTapEnable(tap, True)

    if use_iohid_manager:
        manager_listener = IOHIDManagerListener(
            start_time=start_time,
            target_usage=CAPS_LOCK_USAGE,
        )
        if not manager_listener.start():
            return 1

    initial_alpha, initial_flags = _read_hid_flags()
    print(
        f'READY: tap={config.tap} mode={config.mode} duration={config.duration:.1f}s '
        f'initial_hid_alpha={initial_alpha} initial_hid_flags=0x{initial_flags:08x}',
        flush=True,
    )

    end_time = time.time() + config.duration
    while time.time() < end_time:
        # 每次只跑一个很短的 run loop 切片，保证脚本能按时退出，
        # 同时也能及时把两条回调日志刷到终端。
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.25, False)

    if manager_listener is not None:
        manager_listener.stop()

    final_alpha, final_flags = _read_hid_flags()
    print(
        f'DONE: final_hid_alpha={final_alpha} final_hid_flags=0x{final_flags:08x}',
        flush=True,
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
