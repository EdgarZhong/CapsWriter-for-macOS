# coding: utf-8
"""
WebSocket 管理器 (SocketManager)

负责维护 ASR 服务器的异步通讯层，包括 WebSocket Server 的生命周期管理、
心跳监控、数据发送任务的编排。
"""

import asyncio
import functools
import os
import websockets
from config_server import ServerConfig as Config
from .ws_recv import ws_recv
from .ws_send import ws_send
from .. import logger # Server module logger

# client 断连后等待重连的宽限期（秒）；超时后 server 自动 exit 0，launchd 不重启
# 设为 60s：足以覆盖 launchd 重启 client 的时间（通常 <10s），同时避免 ML 模型被重复加载
_DISCONNECT_GRACE_PERIOD = 60.0


class SocketManager:
    """
    WebSocket 网络管理器

    负责拉起并维护 WebSocket Server 以及识别结果的异步发送任务。
    """
    def __init__(self, app):
        self.app = app
        self._is_running = False
        self._server = None  # websockets.serve 返回的 server 对象

    def _check_port(self):
        """检查端口可用性"""
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((Config.addr, int(Config.port)))
                return True
            except socket.error:
                logger.error(f"端口冲突：{Config.addr}:{Config.port} 已被占用，请检查是否已有服务端正在运行。")
                return False

    async def _watch_connections(self) -> None:
        """监控 WebSocket 连接数，实现 60s 断连宽限期自动退出。

        逻辑：
        - 首次有连接后才开始监控（startup 阶段 client 尚未连接不触发退出）
        - 当所有连接断开时开始计时
        - 60s 内若有新连接则重置计时
        - 超过 60s 无连接则调用 app.stop() 并通过 os._exit(0) 退出
          （os._exit 绕过 asyncio 清理，确保 launchd 看到 exit 0 不重启）
        """
        CHECK_INTERVAL = 2.0
        had_connection = False    # 是否曾经建立过连接
        disconnect_time = None    # 最后一次变为"无连接"状态的时刻

        while True:
            try:
                await asyncio.sleep(CHECK_INTERVAL)
            except asyncio.CancelledError:
                return

            has_connections = bool(self.app.state.sockets)

            if has_connections:
                # 有连接：重置计时
                had_connection = True
                disconnect_time = None
            elif had_connection and disconnect_time is None:
                # 曾经有连接，刚刚断开：开始计时
                disconnect_time = asyncio.get_event_loop().time()
                logger.info(
                    f"[SocketManager] 所有 client 已断连，"
                    f"等待 {_DISCONNECT_GRACE_PERIOD:.0f}s 后自动退出"
                )
            elif disconnect_time is not None:
                elapsed = asyncio.get_event_loop().time() - disconnect_time
                if elapsed >= _DISCONNECT_GRACE_PERIOD:
                    logger.info(
                        f"[SocketManager] 断连宽限期 {_DISCONNECT_GRACE_PERIOD:.0f}s 已到，"
                        "server 自动退出 (exit 0)"
                    )
                    self.app.stop()
                    # 给 stop() 一点时间完成资源清理，再强制退出
                    await asyncio.sleep(1.0)
                    os._exit(0)

    async def start(self):
        """启动 WebSocket 网络服务。"""
        if self._is_running:
            return

        # 启动前自检端口
        if not self._check_port():
            input("\n按回车键退出...")
            return

        self._is_running = True

        loop = self.app.loop

        # 优化守护线程执行器（防止阻塞事件循环）
        from core.tools.daemon_executor import SimpleDaemonExecutor
        loop.set_default_executor(SimpleDaemonExecutor())

        # 准备连接处理器（注入 app 引用）
        handler = functools.partial(ws_recv, app=self.app)

        logger.info(f"正在拉起 WebSocket 服务 (监听: {Config.addr}:{Config.port})")

        async with websockets.serve(
            handler,
            Config.addr,
            Config.port,
            subprotocols=["binary"],
            max_size=None,
        ) as server:
            self._server = server

            # 启动断连监控协程（与 ws_send 并发运行）
            watcher = asyncio.create_task(self._watch_connections())

            logger.info("WebSocket 发送协程已就绪")
            try:
                await ws_send(self.app)
            finally:
                # ws_send 退出后（正常 stop 或异常），取消监控任务
                watcher.cancel()
                try:
                    await watcher
                except asyncio.CancelledError:
                    pass

        self._is_running = False
        logger.info("SocketManager: WebSocket 服务已退出")

    def stop(self):
        """停止 WebSocket 网络服务"""
        # 主动关闭 WebSocket 服务器，让 ws_send 的 await 尽快返回
        if self._server:
            self._server.close()
        self._is_running = False
