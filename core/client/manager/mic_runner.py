# coding: utf-8
import asyncio
from . import logger
from ..ui import TipsDisplay
from config_client import ClientConfig as Config, __version__


class MicRunner:
    """
    麦克风模式运行器：负责麦克风模式下的资源初始化、识别处理器循环及生命周期监控。
    """
    def __init__(self, app):
        self.app = app
        self.processor = None

    @property
    def state(self):
        return self.app.state

    @property
    def ws_manager(self):
        return self.app.ws

    @property
    def tray_manager(self):
        return self.app.tray

    def start_resources(self):
        """初始化麦克风模式特有资源 (音频硬件、快捷键、UI 托盘)"""
        # 1. 托盘
        self.tray_manager.start()

        # 2. UI 提示
        TipsDisplay.show_mic_tips()

        # 3. 开启运行组件（快捷键监听始终需要先启动；音频流是否常驻则由平台策略决定）
        if self.app.stream.should_start_immediately():
            self.app.stream.start()
        else:
            logger.info("当前平台采用按需开流策略，客户端空闲时不预先占用麦克风")
        self.app.shortcut.start()
        # remap 必须在 F18Bridge 启动前建立，否则 Caps Lock 事件不会变成 F18
        if self.app.remap_session is not None:
            self.app.remap_session.start()
        self.app.start_platform_shortcut_bridge()
        
        # 4. 开启 UDP 控制 (如果启用)
        if Config.udp_control:
            self.app.udp.start()

        # 5. 开启后台服务 (热词、LLM)
        self.app.hotword.start()
        if Config.llm_enabled:
            self.app.llm.start()

    async def _heartbeat_task(self) -> None:
        """每 5s 更新 status.json 的 last_heartbeat，供 capswriter status 判断存活。"""
        while True:
            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:
                return
            eb = getattr(self.app, 'error_bus', None)
            if eb:
                eb.heartbeat()
            # 键盘 tap 体检（macOS）：仅凭外部可观测状态发现「回调不会运行」类失效
            # （撤辅助功能/回调死锁/撤输入监控）。复用本 5s 心跳，无需专用守护线程。
            bridge = getattr(self.app, 'macos_caps_bridge', None)
            if bridge is not None:
                try:
                    bridge.check_health()
                except Exception as e:
                    logger.debug(f"键盘体检异常: {e}")

    async def run(self):
        """麦克风模式主入口"""

        logger.info("=" * 50)
        logger.info(f"CapsWriter for macOS {__version__} (麦克风模式)")
        logger.info(f"日志级别: {Config.log_level}")

        # 1. 资源启动
        self.start_resources()

        # 2. 启动核心处理器（内部处理连接与循环）+ 并发心跳任务
        from ..output import ResultProcessor
        self.processor = ResultProcessor(self.app)

        heartbeat = asyncio.create_task(self._heartbeat_task())
        try:
            await self.processor.start()
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            
