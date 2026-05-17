"""
LLM 模块

提供 LLM 相关的所有功能，包括角色管理、消息构建、上下文管理等"""

from core import get_logger
logger = get_logger('client')

def __getattr__(name):
    """
    惰性导出 LLM 子模块对象。

    这个包原本会在导入阶段立刻加载角色、输出、监控、剪贴板等全部子模块。
    在 macOS 客户端调试阶段，这会把大量与当前任务无关的运行期依赖提前执行。
    改为惰性导出后，只有真正访问某个对象时才导入对应模块。
    """
    if name in ('LLMHandler', 'LLMResult'):
        from .llm_handler import LLMHandler, LLMResult
        return {
            'LLMHandler': LLMHandler,
            'LLMResult': LLMResult,
        }[name]

    if name == 'RoleConfig':
        from .llm_role_config import RoleConfig
        return RoleConfig

    if name == 'RoleLoader':
        from .llm_role_loader import RoleLoader
        return RoleLoader

    if name == 'ContextManager':
        from .llm_context import ContextManager
        return ContextManager

    if name == 'MessageBuilder':
        from .llm_message_builder import MessageBuilder
        return MessageBuilder

    if name == 'ClientPool':
        from .llm_client_pool import ClientPool
        return ClientPool

    if name == 'copy_to_clipboard':
        from .llm_clipboard import copy_to_clipboard
        return copy_to_clipboard

    if name in ('get_selected_text', 'record_selection_usage'):
        from .llm_get_selection import get_selected_text, record_selection_usage
        return {
            'get_selected_text': get_selected_text,
            'record_selection_usage': record_selection_usage,
        }[name]

    if name == 'LLMFileWatcher':
        from .llm_watcher import LLMFileWatcher
        return LLMFileWatcher

    if name == 'StopMonitor':
        from .llm_stop_monitor import StopMonitor
        return StopMonitor

    if name == 'handle_toast_mode':
        from .llm_output_toast import handle_toast_mode
        return handle_toast_mode

    if name == 'handle_typing_mode':
        from .llm_output_typing import handle_typing_mode
        return handle_typing_mode

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # 核心
    'LLMHandler',

    # 角色管理
    'RoleConfig',
    'RoleLoader',

    # 上下文
    'ContextManager',

    # 组件
    'MessageBuilder',
    'ClientPool',

    # 剪贴板/选中文字
    'get_selected_text',
    'record_selection_usage',
    'copy_to_clipboard',

    # 监控
    'LLMFileWatcher',
    'StopMonitor',

    # 输出
    'handle_toast_mode',
    'handle_typing_mode',

    # 主入口
    'LLMResult',
]
