# coding: utf-8
"""麦克风生命周期隔离回归：不打开真实设备，不改系统权限。

运行：.venv/bin/python tools/test_stream_stop_leak.py
以受控事件模拟原生关闭卡住/迟到返回/错误码，并检查重试期间资源归属。
"""
from __future__ import annotations

import asyncio
import base64
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config_client import ClientConfig as Config
from core.client.audio.stream import AudioStreamManager
from core.client.audio.recorder import AudioRecorder
from core.client.state import ClientState


class FakeStream:
    """模拟流方法；事件使超时可重复，严格检查 ignore_errors 防止假成功。"""
    def __init__(self, *, blocked=None, error=None, start_error=None):
        self.blocked = blocked
        self.error = error
        self.start_error = start_error
        self.calls = []

    def start(self):
        self.calls.append('start')
        if self.start_error:
            raise self.start_error

    def abort(self, *, ignore_errors):
        self.calls.append(('abort', ignore_errors))

    def close(self, *, ignore_errors):
        self.calls.append(('close', ignore_errors))
        if self.blocked:
            self.blocked.wait(2)
        if self.error:
            raise self.error


def manager(stream=None):
    """使用正式构造函数，以保证新增生命周期字段同生产一致。"""
    app = SimpleNamespace(state=ClientState(), loop=None, error_bus=Mock())
    mgr = AudioStreamManager(app)
    app.state.stream = stream
    mgr._running = stream is not None
    mgr._recording_session_count = int(stream is not None)
    mgr.CLOSE_TIMEOUT = 0.03
    mgr.CALLBACK_STOP_TIMEOUT = 0
    return mgr


class StreamTests(unittest.TestCase):
    """验证成功、原生错误及未完成关闭的真实行为，而非只检查通知存在。"""
    def setUp(self):
        # 原生模拟的30ms超时不应受 Rich traceback 绘制耗时左右，另用日志断言
        # 区分成功/失败，避免把终端渲染当成音频行为。
        self.logs = patch('core.client.audio.stream.logger').start()
        self.addCleanup(patch.stopall)

    def test_close_checks_errors_and_is_idempotent(self):
        stream = FakeStream()
        mgr = manager(stream)
        mgr.stop()
        mgr.stop()
        self.assertEqual(stream.calls, [('abort', False), ('close', False)])
        self.assertIsNone(mgr._closing_stream)
        mgr.app.error_bus.notify.assert_not_called()

    def test_close_failure_keeps_ownership_and_blocks_reload(self):
        stream = FakeStream(error=RuntimeError('native close failed'))
        mgr = manager(stream)
        mgr.stop()
        self.assertIs(mgr._closing_stream, stream)
        with patch('core.client.audio.stream.sd.InputStream') as opened:
            self.assertIsNone(mgr.start())
            opened.assert_not_called()
        with patch('core.client.audio.stream.sd._terminate') as terminate:
            with self.assertRaises(RuntimeError):
                mgr._reload_portaudio()
            terminate.assert_not_called()
        mgr.app.error_bus.notify.assert_called_once()
        mgr.app.error_bus.update.assert_called_with(state='error', microphone_ok=False)

    def test_timeout_retains_stream_until_late_success(self):
        release = threading.Event()
        mgr = manager(FakeStream(blocked=release))
        try:
            mgr.stop()
            self.assertTrue(mgr._close_thread.is_alive())
            self.assertIsNotNone(mgr._closing_stream)
            with patch('core.client.audio.stream.sd.InputStream') as opened:
                self.assertFalse(mgr.start_recording_session())
                opened.assert_not_called()
            mgr.app.error_bus.notify.assert_called_once()
        finally:
            release.set()
            mgr._close_thread.join(1)
        self.assertIsNone(mgr._closing_stream)
        self.assertFalse(mgr._close_error)

    def test_callback_exits_without_enqueue_after_stop(self):
        import sounddevice as sd
        mgr = manager()
        mgr._stop_requested.set()
        with self.assertRaises(sd.CallbackAbort):
            mgr._audio_callback(np.ones((960, 1)), 960, None, None)

    def test_finished_callback_never_reopens_in_native_stack(self):
        mgr = manager(FakeStream())
        mgr.app.loop = Mock()
        with patch.object(mgr, 'reopen') as reopen:
            mgr._on_stream_finished()
            self.assertTrue(mgr._stream_finished.is_set())
            reopen.assert_not_called()
            mgr.app.loop.call_soon_threadsafe.assert_called_once()

    def test_normal_start_does_not_refresh_and_uses_low_latency(self):
        mgr = manager()
        stream = FakeStream()
        with patch('core.client.audio.stream.platform.system', return_value='Darwin'), \
             patch.multiple(Config, macos_mic_device='builtin'), \
             patch.object(mgr, '_find_builtin_mic', return_value=0), \
             patch('core.client.audio.stream.sd.query_devices', return_value={'max_input_channels': 1}), \
             patch('core.client.audio.stream.sd.InputStream', return_value=stream) as opened, \
             patch.object(mgr, '_reload_portaudio') as reload:
            self.assertTrue(mgr.start_recording_session())
            reload.assert_not_called()
            self.assertEqual(opened.call_args.kwargs['blocksize'], 960)
            self.assertEqual(opened.call_args.kwargs['latency'], 'low')
            self.assertEqual(mgr._recording_session_count, 1)
            mgr.stop_recording_session()
        self.assertIsNone(mgr._closing_stream)

    def test_start_failure_closes_before_single_retry(self):
        mgr = manager()
        failed = FakeStream(start_error=RuntimeError('device changed'))
        good = FakeStream()
        with patch('core.client.audio.stream.platform.system', return_value='Darwin'), \
             patch.multiple(Config, macos_mic_device='builtin'), \
             patch.object(mgr, '_find_builtin_mic', return_value=0), \
             patch('core.client.audio.stream.sd.query_devices', return_value={'max_input_channels': 1}), \
             patch('core.client.audio.stream.sd.InputStream', side_effect=[failed, good]), \
             patch.object(mgr, '_reload_portaudio') as reload:
            self.assertTrue(mgr.start_recording_session())
            self.assertEqual(failed.calls[-1], ('close', False))
            reload.assert_called_once()
            self.assertEqual(mgr._recording_session_count, 1)
            mgr.stop_recording_session()

    def test_failed_start_with_failed_close_cannot_retry(self):
        mgr = manager()
        failed = FakeStream(start_error=RuntimeError('start failed'), error=RuntimeError('close failed'))
        with patch('core.client.audio.stream.platform.system', return_value='Darwin'), \
             patch.multiple(Config, macos_mic_device='builtin'), \
             patch.object(mgr, '_find_builtin_mic', return_value=0), \
             patch('core.client.audio.stream.sd.query_devices', return_value={'max_input_channels': 1}), \
             patch('core.client.audio.stream.sd.InputStream', return_value=failed) as opened, \
             patch.object(mgr, '_reload_portaudio') as reload:
            self.assertFalse(mgr.start_recording_session())
            self.assertEqual(opened.call_count, 1)
            reload.assert_not_called()
            self.assertEqual(mgr._recording_session_count, 0)

    def test_callback_defers_metrics_and_copies_audio(self):
        mgr = manager()
        mgr.app.loop = Mock()
        mgr.state.queue_in = asyncio.Queue()
        mgr.state.start_recording(1.0, trace_id='one')
        data = np.ones((960, 1), dtype=np.float32)
        with patch.object(mgr.state, 'mark_audio_metrics') as metrics:
            mgr._audio_callback(data, 960, None, None)
            metrics.assert_not_called()
            callback, copied, frames, ts, trace, status, queue_in = mgr.app.loop.call_soon_threadsafe.call_args.args
            data[:] = 0
            callback(copied, frames, ts, trace, status, queue_in)
            metrics.assert_called_once()
        np.testing.assert_array_equal(mgr.state.queue_in.get_nowait()['data'], np.ones((960, 1)))

    def test_late_callback_keeps_original_queue(self):
        mgr = manager()
        mgr.app.loop = Mock()
        old_queue = mgr.state.queue_in
        mgr.state.start_recording(1., trace_id='old')
        mgr._audio_callback(np.ones((960, 1)), 960, None, None)
        callback, *args = mgr.app.loop.call_soon_threadsafe.call_args.args
        mgr.state.stop_recording()
        mgr.state.queue_in = asyncio.Queue()
        callback(*args)
        self.assertEqual(old_queue.qsize(), 1)
        self.assertTrue(mgr.state.queue_in.empty())

    def test_non_macos_keeps_blocksize_and_default_latency(self):
        mgr = manager()
        with patch('core.client.audio.stream.platform.system', return_value='Windows'), \
             patch('core.client.audio.stream.sd.query_devices', return_value={'max_input_channels': 1}), \
             patch('core.client.audio.stream.sd.InputStream', return_value=FakeStream()) as opened:
            self.assertTrue(mgr.start_recording_session())
            self.assertEqual(opened.call_args.kwargs['blocksize'], 2400)
            self.assertNotIn('latency', opened.call_args.kwargs)
            mgr.stop_recording_session()
            self.assertTrue(mgr._running, '常驻模式录音结束不关闭设备')
            mgr.stop()

    def test_default_mode_refreshes_and_follows_system_default(self):
        """发布默认 default 模式：每次开流刷新设备表，不找内建麦，跟随系统默认输入。"""
        mgr = manager()
        with patch('core.client.audio.stream.platform.system', return_value='Darwin'), \
             patch.multiple(Config, macos_mic_device='default'), \
             patch.object(mgr, '_find_builtin_mic') as find_builtin, \
             patch.object(mgr, '_reload_portaudio') as reload, \
             patch('core.client.audio.stream.sd.query_devices', return_value={'max_input_channels': 1}), \
             patch('core.client.audio.stream.sd.InputStream', return_value=FakeStream()) as opened:
            self.assertTrue(mgr.start_recording_session())
            reload.assert_called_once()
            find_builtin.assert_not_called()
            self.assertIsNone(opened.call_args.kwargs['device'])
            mgr.stop_recording_session()

    def test_builtin_mode_refreshes_once_when_builtin_missing(self):
        """builtin 模式找不到内建麦：刷新设备表重找一次，仍无则回退默认输入。"""
        mgr = manager()
        with patch('core.client.audio.stream.platform.system', return_value='Darwin'), \
             patch.multiple(Config, macos_mic_device='builtin'), \
             patch.object(mgr, '_find_builtin_mic', return_value=None) as find_builtin, \
             patch.object(mgr, '_reload_portaudio') as reload, \
             patch('core.client.audio.stream.sd.query_devices', return_value={'max_input_channels': 1}), \
             patch('core.client.audio.stream.sd.InputStream', return_value=FakeStream()) as opened:
            self.assertTrue(mgr.start_recording_session())
            reload.assert_called_once()
            self.assertEqual(find_builtin.call_count, 2)
            self.assertIsNone(opened.call_args.kwargs['device'])
            mgr.stop_recording_session()


class RecorderTests(unittest.IsolatedAsyncioTestCase):
    """解码实际发送的PCM，确认跨阈值的当前块没有被丢弃或重复。"""
    async def test_cross_threshold_preserves_every_block(self):
        state = ClientState()
        state.queue_in = asyncio.Queue()
        recorder = AudioRecorder(SimpleNamespace(state=state))
        recorder._send_message = AsyncMock()
        state.queue_in.put_nowait({'type': 'begin', 'time': 1., 'trace_id': 'test'})
        for timestamp, value in [(1.1, 1.), (1.2, 2.), (1.31, 3.), (1.4, 4.)]:
            state.queue_in.put_nowait({'type': 'data', 'time': timestamp,
                                      'data': np.full((6, 1), value, dtype=np.float32)})
        state.queue_in.put_nowait({'type': 'finish', 'time': 1.5})
        with patch.multiple(Config, save_audio=False, threshold=0.3):
            await recorder.record_and_send()
            await asyncio.sleep(0)
        messages = [call.args[0] for call in recorder._send_message.await_args_list]
        pcm = np.concatenate([np.frombuffer(base64.b64decode(m.data), dtype=np.float32)
                              for m in messages if m.data])
        np.testing.assert_array_equal(pcm, [1., 1., 2., 2., 3., 3., 4., 4.])
        self.assertTrue(messages[-1].is_final)
        self.assertAlmostEqual(recorder._duration, 24 / 48000)


if __name__ == '__main__':
    unittest.main(verbosity=2)
