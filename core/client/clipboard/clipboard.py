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
from pynput import keyboard
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


async def paste_text(text: str, restore_clipboard: bool = True):
    """
    通过模拟 Ctrl+V 粘贴文本

    Args:
        text: 要粘贴的文本
        restore_clipboard: 粘贴后是否恢复原剪贴板内容
    """
    # 保存剪切板
    original: Optional[str] = None
    if restore_clipboard:
        try:
            original = safe_paste()
        except Exception as e:
            logger.warning(f"读取原始剪贴板失败，跳过恢复流程: {e}")

    # 复制要粘贴的文本
    safe_copy(text)
    logger.debug(f"已复制文本到剪贴板，长度: {len(text)}")

    # 粘贴结果（使用 pynput 模拟 Ctrl+V）
    controller = keyboard.Controller()
    if platform.system() == 'Darwin':
        # macOS: Command+V
        with controller.pressed(keyboard.Key.cmd):
            controller.tap('v')
    else:
        # Windows/Linux: Ctrl+V
        with controller.pressed(keyboard.Key.ctrl):
            controller.tap('v')
    
    logger.debug("已发送粘贴命令 (Ctrl+V)")

    # 还原剪贴板
    if restore_clipboard and original is not None:
        await asyncio.sleep(0.1)
        if safe_copy(original):
            logger.debug("剪贴板已恢复")
