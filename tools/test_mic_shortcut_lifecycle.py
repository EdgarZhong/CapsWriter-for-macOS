# coding: utf-8
"""用受控线程/事件循环复现普通松手的两个启动空窗，不触发真实键盘或麦克风。"""
from __future__ import annotations

import asyncio
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.client.shortcut.macos_caps_controller import MacOSCapsController
from core.client.shortcut.shortcut_config import Shortcut
from core.client.shortcut.task import ShortcutTask
from core.client.state import ClientState


class ControllerTests(unittest.TestCase):
    """真实 Timer 由手动触发替代，只测开始/停止顺序，不依赖机器调度速度。"""
    def test_release_while_start_is_blocked_is_ordered(self):
        entered, unblock, stopped = threading.Event(), threading.Event(), threading.Event()
        actions = []

        def start():
            entered.set()
            unblock.wait(2)
            actions.append('start')

        def stop():
            actions.append('stop')
            stopped.set()

        controller = MacOSCapsController(start, stop, Mock())
        with patch('core.client.shortcut.macos_caps_controller.threading.Timer'):
            controller.on_f18_down()
            controller._on_hold_threshold(controller._press_id)
            self.assertTrue(entered.wait(1))
            try:
                controller.on_f18_up()
                self.assertFalse(stopped.is_set(), '不能在开始尚未发布时先消耗停止')
            finally:
                unblock.set()
            self.assertTrue(stopped.wait(1))
        self.assertEqual(actions, ['start', 'stop'])

    def test_short_tap_and_stale_timer_do_not_record(self):
        start, stop = Mock(), Mock()
        toggled = threading.Event()
        controller = MacOSCapsController(start, stop, toggled.set)
        with patch('core.client.shortcut.macos_caps_controller.threading.Timer'):
            controller.on_f18_down()
            old_id = controller._press_id
            controller.on_f18_up()
            self.assertTrue(toggled.wait(1))
            controller.on_f18_down()
            controller._on_hold_threshold(old_id)
            controller.on_f18_up()
            controller._actions.join()
        start.assert_not_called()
        stop.assert_not_called()
        self.assertEqual(controller._hold_threshold_s, 0.2)

    def test_shutdown_invalidates_timer_and_queued_start(self):
        entered, unblock = threading.Event(), threading.Event()
        start, stop = Mock(), Mock()

        def blocked_toggle():
            entered.set()
            unblock.wait(2)
        controller = MacOSCapsController(start, stop, blocked_toggle)
        with patch('core.client.shortcut.macos_caps_controller.threading.Timer'):
            controller.on_f18_down()
            controller.on_f18_up()
            self.assertTrue(entered.wait(1))
            controller.on_f18_down()
            token = controller._press_id
            controller._on_hold_threshold(token)
            controller.stop()
            controller._on_hold_threshold(token)
            controller.on_f18_down()
            unblock.set()
            controller._action_worker.join(1)
        self.assertFalse(controller._action_worker.is_alive())
        start.assert_not_called()
        stop.assert_called_once()


class ShortcutTests(unittest.IsolatedAsyncioTestCase):
    """使用真实异步队列，消费 begin/data/finish 并检查最终录音状态。"""
    async def asyncSetUp(self):
        self.messages = []
        self.state = ClientState()
        self.state.queue_in = asyncio.Queue()
        self.app = SimpleNamespace(
            state=self.state, loop=asyncio.get_running_loop(),
            stream=Mock(start_recording_session=Mock(return_value=True)),
        )
        self.task = ShortcutTask(self.app, Shortcut('caps_lock', suppress=True))
        self.task._status = Mock()
        self.task._should_restore_after_finish = Mock(return_value=False)
        self.task._get_recorder = self.make_recorder
        # 捕获前台应用与这些纯状态机测试无关，不访问真实 macOS 应用。
        patch('core.client.shortcut.task.platform.system', return_value='Linux').start()
        patch('core.client.shortcut.task.logger').start()
        self.addCleanup(patch.stopall)

    def make_recorder(self):
        """每个消费者冻结一份队列，模拟正式 AudioRecorder 的会话隔离。"""
        queue = self.state.queue_in
        messages = []
        self.messages.append(messages)

        async def consume():
            while True:
                item = await queue.get()
                messages.append(item['type'])
                if item['type'] == 'finish':
                    return
        return SimpleNamespace(record_and_send=consume)

    async def settle(self):
        """消费者完成作为断言屏障，不靠固定 sleep 等待状态机碰巧跑完。"""
        if self.task.task is not None:
            await asyncio.wait_for(asyncio.wrap_future(self.task.task), 1)

    async def test_release_during_open(self):
        entered, unblock = threading.Event(), threading.Event()

        def open_stream():
            entered.set()
            unblock.wait(2)
            return True
        self.app.stream.start_recording_session.side_effect = open_stream
        launched = asyncio.create_task(asyncio.to_thread(self.task.launch))
        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
        self.task.request_finish()
        unblock.set()
        await launched
        await self.settle()
        self.assertEqual(self.messages, [['begin', 'finish']])
        self.assertFalse(self.state.recording)
        self.app.stream.stop_recording_session.assert_called_once()

    async def test_release_during_state_publication(self):
        entered, unblock = threading.Event(), threading.Event()
        original = self.state.start_recording

        def publish(*args, **kwargs):
            entered.set()
            unblock.wait(2)
            original(*args, **kwargs)
        self.state.start_recording = publish
        launched = asyncio.create_task(asyncio.to_thread(self.task.launch))
        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
        self.task.request_finish()
        unblock.set()
        await launched
        await self.settle()
        self.assertFalse(self.state.recording, '松手后初始化不得重新置为录音')
        self.assertEqual(self.messages, [['begin', 'finish']])
        self.app.stream.stop_recording_session.assert_called_once()

    async def test_duplicate_finish_closes_once(self):
        await asyncio.to_thread(self.task.launch)
        await asyncio.gather(*[asyncio.to_thread(self.task.request_finish) for _ in range(3)])
        await self.settle()
        self.app.stream.stop_recording_session.assert_called_once()
        self.assertEqual(self.messages, [['begin', 'finish']])

    async def test_cancel_during_open_does_not_submit_finish(self):
        entered, unblock = threading.Event(), threading.Event()

        def open_stream():
            entered.set()
            unblock.wait(2)
            return True
        self.app.stream.start_recording_session.side_effect = open_stream
        launched = asyncio.create_task(asyncio.to_thread(self.task.launch))
        self.assertTrue(await asyncio.to_thread(entered.wait, 1))
        self.task.cancel()
        unblock.set()
        await launched
        self.assertIsNone(self.task.task)
        self.assertFalse(self.state.recording)
        self.assertNotIn('finish', self.messages[0])
        self.app.stream.stop_recording_session.assert_called_once()

    async def test_trace_failure_cannot_skip_device_close_or_finish(self):
        await asyncio.to_thread(self.task.launch)
        self.state.mark_recording_finish_requested = Mock(side_effect=RuntimeError('trace failed'))
        await asyncio.to_thread(self.task.request_finish)
        await self.settle()
        self.app.stream.stop_recording_session.assert_called_once()
        self.assertEqual(self.messages, [['begin', 'finish']])

    async def test_startup_failure_is_clean_and_next_queue_is_isolated(self):
        original = self.state.start_recording
        self.state.start_recording = Mock(side_effect=RuntimeError('publication failed'))
        await asyncio.to_thread(self.task.launch)
        failed_queue = self.state.queue_in
        self.assertFalse(self.task._launching)
        self.assertFalse(self.task.is_recording)
        self.app.stream.stop_recording_session.assert_called_once()
        self.state.start_recording = original
        await asyncio.to_thread(self.task.launch)
        self.assertIsNot(failed_queue, self.state.queue_in)
        await asyncio.to_thread(self.task.request_finish)
        await self.settle()
        self.assertEqual(self.messages[-1], ['begin', 'finish'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
