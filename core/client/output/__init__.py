# coding: utf-8
"""
output 子模块

包含识别结果输出相关功能。
"""

from .. import logger


def __getattr__(name):
    """
    惰性导出 output 子模块对象。

    避免在仅导入 `text_output` 等局部模块时，又被包初始化提前拉起
    `ResultProcessor` 及其关联的大量运行期依赖。
    """
    if name == 'ResultProcessor':
        from core.client.output.result_processor import ResultProcessor
        return ResultProcessor

    if name == 'TextOutput':
        from core.client.output.text_output import TextOutput
        return TextOutput

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    'logger',
    'ResultProcessor',
    'TextOutput',
]
