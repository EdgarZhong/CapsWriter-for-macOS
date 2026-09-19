# coding: utf-8
"""
剪贴板工具模块

提供统一的剪贴板操作接口，包括：
1. 安全读取剪贴板（支持多种编码）
2. 安全写入剪贴板
3. 剪贴板保存/恢复上下文管理器
4. 粘贴文本（模拟 Ctrl+V）
"""
import asyncio
import platform
import subprocess
from contextlib import contextmanager
from typing import Optional
import pyclip
from . import logger


# 支持的编码列表
CLIPBOARD_ENCODINGS = ['utf-8', 'gbk', 'utf-16', 'latin1']


def _read_clipboard_raw() -> bytes:
    """
    读取原始剪贴板字节流。

    设计说明：
    1. macOS 下优先走 `pbpaste` 子进程，避免在主进程里直接通过 `pyclip`
       触碰 Pasteboard / CoreFoundation 对象，尽量降低 `CFDataValidateRange`
       这类底层断言干扰主程序的概率。
    2. 其他平台继续复用现有 `pyclip` 行为，保持兼容性。
    """
    if platform.system() == 'Darwin':
        result = subprocess.run(
            ['pbpaste'],
            check=True,
            capture_output=True,
        )
        return result.stdout

    clipboard_data = pyclip.paste()
    if isinstance(clipboard_data, bytes):
        return clipboard_data
    if isinstance(clipboard_data, str):
        return clipboard_data.encode('utf-8')
    return b''


def _write_clipboard_raw(data: bytes) -> None:
    """
    写入原始剪贴板字节流。

    设计说明：
    1. macOS 下统一改走 `pbcopy`，让系统剪贴板交互发生在独立子进程里。
    2. 这里保留 bytes 级接口，是为了后续如需恢复“非 UTF-8 文本”时仍有
       明确边界；当前上层主要传入的仍然是 UTF-8 文本字节。
    """
    if platform.system() == 'Darwin':
        subprocess.run(
            ['pbcopy'],
            input=data,
            check=True,
        )
        return

    pyclip.copy(data)


def _decode_clipboard_bytes(clipboard_data: bytes) -> str:
    """
    将剪贴板字节流尽量解码为字符串。

    这里保留原有“多编码兜底”的策略，避免历史中文环境下的剪贴板内容
    因编码不一致直接丢失。
    """
    for encoding in CLIPBOARD_ENCODINGS:
        try:
            return clipboard_data.decode(encoding)
        except UnicodeDecodeError:
            continue

    logger.debug(f"剪贴板解码失败，尝试了编码: {CLIPBOARD_ENCODINGS}")
    return ""


def safe_paste() -> str:
    """
    安全地从剪贴板读取并解码文本

    尝试多种编码方式，确保能够正确读取

    Returns:
        解码后的文本字符串，失败返回空字符串
    """
    try:
        clipboard_data = _read_clipboard_raw()
        if not clipboard_data:
            return ""
        return _decode_clipboard_bytes(clipboard_data)

    except Exception as e:
        logger.warning(f"剪贴板读取失败: {e}")
        return ""


def safe_copy(content: Optional[str]) -> bool:
    """
    安全地复制内容到剪贴板

    Args:
        content: 要复制的内容

    Returns:
        是否成功
    """
    # 这里不再把空字符串视为“非法输入”。
    # 原因是 macOS 下恢复剪贴板时，原内容本来就可能是空串；如果直接跳过，
    # 会把“清空前的临时识别结果”残留在系统剪贴板里。
    if content is None:
        return False

    try:
        _write_clipboard_raw(content.encode('utf-8'))
        logger.debug(f"剪贴板写入成功，长度: {len(content)}")
        return True
    except Exception as e:
        logger.warning(f"剪贴板写入失败: {e}")
        return False


def copy_to_clipboard(content: str):
    """
    复制内容到剪贴板（兼容旧 API）

    Args:
        content: 要复制的内容
    """
    safe_copy(content)


def _post_macos_paste_shortcut() -> bool:
    """用进程内 Quartz 直接发送 Cmd+V，成功返回 True。

    旧路径每次启动 `osascript` 再让 System Events 注入按键，单次进程启动就会
    引入约 175ms 延迟。CapsWriter 已持有辅助功能权限，直接发布 CGEvent 能保留
    相同权限边界，并把正常路径的注入开销降到毫秒级。异常时由调用方回退旧路径。
    """
    try:
        import Quartz

        source = Quartz.CGEventSourceCreate(
            Quartz.kCGEventSourceStateHIDSystemState)
        # ANSI V 的 macOS 虚拟键码固定为 0x09；down/up 都带 Command 标志，
        # 避免目标应用把事件解释成普通字母 v。
        key_down = Quartz.CGEventCreateKeyboardEvent(source, 0x09, True)
        key_up = Quartz.CGEventCreateKeyboardEvent(source, 0x09, False)
        Quartz.CGEventSetFlags(key_down, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventSetFlags(key_up, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, key_down)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, key_up)
        return True
    except Exception as e:
        logger.warning(f"Quartz Cmd+V 注入失败，回退 osascript: {e}")
        return False


@contextmanager
def save_and_restore_clipboard():
    """
    剪贴板保存/恢复上下文管理器

    用法:
        with save_and_restore_clipboard():
            # 在这里操作剪贴板
            pyclip.copy("临时内容")
        # 退出后剪贴板恢复原内容
    """
    original = safe_paste()
    try:
        yield
    finally:
        if safe_copy(original):
            logger.debug("剪贴板已恢复")


async def paste_text(text: str, restore_clipboard: bool = True) -> bool:
    """
    通过模拟 Ctrl+V 粘贴文本

    Args:
        text: 要粘贴的文本
        restore_clipboard: 粘贴后是否恢复原剪贴板内容

    Returns:
        文本是否已成功写入剪贴板。macOS 的 Cmd+V 注入可能因辅助功能权限失败，
        但此时用户仍可手动粘贴，因此只要复制成功就返回 True。
    """
    is_macos = platform.system() == 'Darwin'

    # macOS 产品口径本来就不恢复旧剪贴板，因此不能为一个不会使用的值额外启动
    # pbpaste 子进程；其它平台仍按原行为保存并在粘贴后恢复。
    original: Optional[str] = None
    if restore_clipboard and not is_macos:
        try:
            original = safe_paste()
        except Exception as e:
            logger.warning(f"读取原始剪贴板失败，跳过恢复流程: {e}")

    # 复制要粘贴的文本
    # “输出成功”的语义边界是目标文本已进入剪贴板。写入失败时绝不能继续发送
    # Cmd+V，否则会把用户原有剪贴板内容误粘贴到前台应用。
    if not safe_copy(text):
        logger.warning("识别文本写入剪贴板失败，已跳过自动粘贴")
        return False
    logger.debug(f"已复制文本到剪贴板，长度: {len(text)}")

    # 粘贴结果
    if is_macos:
        # 正常路径直接在进程内发 Quartz 事件；只有 PyObjC/事件创建异常才启动
        # 较慢的 AppleScript 兜底，避免每次 Enter 都支付固定进程启动成本。
        if not _post_macos_paste_shortcut():
            result = subprocess.run(
                ['osascript', '-e',
                 'tell application "System Events" to keystroke "v" using command down'],
                check=False,
                capture_output=True,
            )
            if result.returncode != 0:
                stderr = result.stderr.decode().strip()
                if '1002' in stderr or 'not allowed' in stderr.lower() or '不允许' in stderr:
                    logger.warning(
                        "自动粘贴失败（缺少辅助功能权限）。"
                        "请前往：系统设置 → 隐私与安全性 → 辅助功能 → 添加运行 client 的终端 app，"
                        "然后重启 client。识别结果已写入剪贴板，可手动 Cmd+V 粘贴。"
                    )
                else:
                    logger.warning(f"osascript 粘贴失败: {stderr}")
    else:
        # Windows/Linux: pynput 模拟 Ctrl+V
        from pynput import keyboard as _kb
        controller = _kb.Controller()
        with controller.pressed(_kb.Key.ctrl):
            controller.tap('v')

    logger.debug("已发送粘贴命令")

    # macOS 下不恢复剪贴板：识别结果应保留在剪贴板，
    # 让用户在 osascript 粘贴失败时仍可手动 Cmd+V 或通过 Maccy 等工具回看。
    if restore_clipboard and original is not None and not is_macos:
        await asyncio.sleep(0.1)
        if safe_copy(original):
            logger.debug("剪贴板已恢复")

    return True
