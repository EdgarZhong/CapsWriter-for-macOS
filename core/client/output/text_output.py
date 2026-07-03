# coding: utf-8
"""
文本输出模块

提供 TextOutput 类用于将识别结果输出到当前窗口。
"""

from __future__ import annotations

import platform
from typing import Optional
import re

from config_client import ClientConfig as Config
from core.client.clipboard.clipboard import paste_text
from . import logger



class TextOutput:
    """
    文本输出器
    
    提供文本输出功能，支持模拟打字和粘贴两种方式。
    """
    
    @staticmethod
    def strip_punc(text: str) -> str:
        """
        消除末尾最后一个标点
        
        Args:
            text: 原始文本
            
        Returns:
            去除末尾标点后的文本
        """
        if not text or not Config.trash_punc:
            return text
        clean_text = re.sub(f"(?<=.)[{Config.trash_punc}]$", "", text)
        return clean_text
    
    async def output(self, text: str, paste: Optional[bool] = None) -> None:
        """
        输出识别结果
        
        根据配置选择使用模拟打字或粘贴方式输出文本。
        
        Args:
            text: 要输出的文本
            paste: 是否使用粘贴方式（None 表示使用配置值）
        """
        if not text:
            return
        
        # 确定输出方式
        if paste is None:
            paste = Config.paste

        if platform.system() == 'Darwin':
            await self._output_macos(text, force_clipboard=bool(paste))
            return
        
        if paste:
            await self._paste_text(text)
        else:
            self._type_text(text)

    async def _output_macos(self, text: str, force_clipboard: bool = False) -> None:
        """macOS 输出：默认保留剪贴板粘贴，可显式切换到 Quartz 注入。"""
        backend = str(getattr(Config, 'macos_output_backend', 'clipboard')).lower()
        if backend in ('paste', 'clip'):
            backend = 'clipboard'

        if force_clipboard or backend == 'clipboard':
            await self._paste_text(text)
            return

        if backend not in ('quartz', 'auto'):
            logger.warning(f"未知 macOS 输出方式: {backend}，回退到 clipboard")
            await self._paste_text(text)
            return

        try:
            from core.client.output.macos_quartz import (
                QuartzOutputOptions,
                QuartzTextInjector,
            )

            options = QuartzOutputOptions(
                chunk_size=getattr(Config, 'macos_quartz_chunk_size', 8),
                key_delay=getattr(Config, 'macos_quartz_key_delay', 0.002),
            )
            QuartzTextInjector(options).type_text(text)
            return
        except Exception as e:
            logger.warning(f"Quartz 文本注入失败: {e}", exc_info=True)

        if backend == 'auto' or getattr(Config, 'macos_clipboard_fallback', False):
            logger.warning("本次将使用剪贴板粘贴作为 fallback")
            await self._paste_text(text)
        else:
            logger.warning("未启用剪贴板 fallback，跳过本次自动上屏以避免污染剪贴板")
    
    async def _paste_text(self, text: str) -> None:
        """
        通过粘贴方式输出文本
        
        Args:
            text: 要粘贴的文本
        """
        logger.debug(f"使用粘贴方式输出文本，长度: {len(text)}")
        await paste_text(text, restore_clipboard=Config.restore_clip)
    
    def _type_text(self, text: str) -> None:
        """
        通过模拟打字方式输出文本

        使用 keyboard.write 替代 pynput.keyboard.Controller.type()，
        避免与中文输入法冲突。

        Args:
            text: 要输出的文本
        """
        logger.debug(f"使用打字方式输出文本，长度: {len(text)}")

        # 非 macOS 平台继续保留原有 `keyboard.write` 行为。
        # 这里改为局部导入，避免在 macOS 上因为导入 `keyboard` 产生副作用。
        import keyboard

        keyboard.write(text)
