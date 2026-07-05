# coding: utf-8
"""
CapsWriter Offline 客户端主程序门面类 (Facade)

采用外观模式统一管理音频流 (AudioStreamManager)、
识别结果处理 (ResultProcessor) 和快捷键管理 (ShortcutManager)。
"""

import os
import sys
import asyncio
from pathlib import Path
from platform import system
from typing import TYPE_CHECKING, Optional

from .state import ClientState
from . import logger
from config_client import ClientConfig as Config, __version__
from core.tools.signal_handler import register_signal
from .state import console
from .connection import WebSocketManager
from typing import TYPE_CHECKING, Optional
from .manager import (
    TrayManager,
    MicRunner, FileRunner
)
from .audio.stream import AudioStreamManager
from .shortcut.shortcut_manager import ShortcutManager
from .shortcut.shortcut_config import Shortcut
from .udp.udp_control import UDPController
from .hotword.manager import HotwordManager
from .llm.llm_handler import LLMHandler
from .output.text_output import TextOutput
from .diary.diary_writer import DiaryWriter
from core.tools.empty_working_set import empty_current_working_set
if TYPE_CHECKING:
    from .shortcut.macos_caps_f18 import MacOSCapsF18Bridge


class CapsWriterClient:
    """
    CapsWriter 客户端门面类

    管理的外部接口简洁：start()。
    """
    def __init__(self, error_bus=None):
        # ErrorBus 实例（可选），用于写 status.json 和发系统通知
        # macOS .app 入口在主线程创建后注入；其他平台为 None
        self.error_bus = error_bus

        # 确保正确的工作目录
        self.base_dir = Path(__file__).parents[2]
        os.chdir(self.base_dir)
            
        # 初始化事件循环
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
            
        # 初始化状态容器
        self.state = ClientState(app=self)

        # 初始化热词管理器
        self.hotword = HotwordManager(
            hotword_files=None,
            threshold=Config.hot_thresh,
            similar_threshold=Config.hot_similar
        )

        # 4. 初始化 LLM 润色系统
        self.llm = LLMHandler(app=self)
        
        self.output = TextOutput()
        self.diary = DiaryWriter(base_path=self.base_dir)

        # 初始化各管理器
        self.ws = WebSocketManager(self)
        self.tray = TrayManager(self)

        # 实例化硬件资源管理组件
        self.stream = AudioStreamManager(self)
        self.shortcut = ShortcutManager(self, [Shortcut(**sc) for sc in Config.shortcuts])
        self.udp = UDPController(self.shortcut)
        self.macos_caps_bridge: Optional[MacOSCapsF18Bridge] = None
        self.remap_session = None  # macOS remap 生命周期由 client 自身持有

        # 只有配置里确实启用了 Caps Lock 快捷键时，才启动 Caps 专用的 remap/F18 桥接。
        # 这样用户把快捷键改成 right ctrl、鼠标侧键等普通输入时，即使忘了同步把
        # macos_caps_mode 改为 off，也不会误写系统级 Caps Lock -> F18 映射。
        has_enabled_caps_shortcut = any(
            shortcut.enabled and shortcut.type == 'keyboard' and shortcut.key == 'caps_lock'
            for shortcut in self.shortcut.shortcuts
        )
        # 注意：macos_caps_remap_enabled 当前与 macos_caps_mode 的职责重叠，历史上也没有
        # 参与主流程判断。这里先保留既有语义，只按 mode + 是否实际使用 Caps Lock 决定；
        # 后续是否合并/废弃这两个配置，留给仓库维护者统一收敛。
        if (
            system() == 'Darwin'
            and has_enabled_caps_shortcut
            and getattr(Config, 'macos_caps_mode', 'off') == 'remap_f18'
        ):
            from .shortcut.macos_caps_f18 import MacOSCapsF18Bridge
            from .shortcut.macos_caps_remap import MacOSCapsRemapSession

            # client 是 Caps Lock → F18 remap 的唯一生命周期 owner
            self.remap_session = MacOSCapsRemapSession()
            self.macos_caps_bridge = MacOSCapsF18Bridge(self)

        # 内存清理
        empty_current_working_set()

    def start_platform_shortcut_bridge(self) -> None:
        """启动平台专用的快捷键桥接器。"""
        if self.macos_caps_bridge is not None:
            self.macos_caps_bridge.start()

    def stop_platform_shortcut_bridge(self) -> None:
        """停止平台专用的快捷键桥接器。"""
        if self.macos_caps_bridge is not None:
            self.macos_caps_bridge.stop()

    def stop(self):
        """
        统一释放所有资源（清理顺序：硬件 -> 托盘 -> WebSocket -> State）
        """
        logger.info("正在执行 CapsWriterClient 资源释放...")

        # 1. 停止核心运行组件
        self.udp.stop()
        self.stop_platform_shortcut_bridge()
        # F18Bridge 停止后再恢复 remap，保证不会收到残留 F18 事件
        if self.remap_session is not None:
            try:
                self.remap_session.restore()
            except Exception as e:
                logger.warning("remap restore failed: %s", e)
        self.shortcut.stop()
        self.stream.stop()

        # 2. 托盘资源
        self.tray.stop()

        # 3. 关闭监控
        self.hotword.stop()
        self.llm.stop()

        # 4. 关闭 WebSocket 连接
        self.ws.close_sync()

        # 5. 重置 State
        try:
            self.state.reset()
        except Exception as e:
            logger.warning(f"重置状态时发生错误: {e}")

        # 6. 停止事件循环（最后一步，确保前面的异步操作已调度）
        self.loop.stop()

        logger.info("资源释放完成")
        console.print('[green4]再见！')


    def start(self, register_signals: bool = True):
        """
        启动客户端 (唯一入口)

        自动根据命令行参数识别模式。内部管理异步循环。

        Args:
            register_signals: 是否注册信号处理。macOS .app 入口在主线程
                自行处理信号，子线程不可调用 signal.signal()，应传 False。
        """

        # 注册退出函数（macOS .app 模式下由外部主线程处理）
        if register_signals:
            register_signal(self.stop)

        files = [Path(f) for f in sys.argv[1:] if os.path.exists(f)]

        if files:
            # 文件转录模式
            runner = FileRunner(self, files)
        else:
            # 麦克风实时模式
            runner = MicRunner(self)
        
        try:
            self.loop.run_until_complete(runner.run())
        except RuntimeError:
            ...
