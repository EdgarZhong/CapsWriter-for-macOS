# coding: utf-8
"""服务端调度回归：用受控队列检查等待与顺序，不加载模型或占用麦克风。"""
from collections import deque
from pathlib import Path
import queue
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.server.schema import Work, Result
from core.server.state import WorkerState
from core.server.worker.work_handler import WorkBuffer, WorkHandler


def make_work(task_id='first', socket_id='mic', offset=0, final=False):
    """偏移作为包序号，使断言同时检查任务顺序与任务内部音频完整性。"""
    return Work('mic', b'', offset, 0, task_id, socket_id, final, 0, 0)


class ObservedQueue:
    """用虚拟等待统计代替真实sleep，精确暴露每包20ms的累积成本。"""
    def __init__(self, items=(), on_empty=None):
        self.items = deque(items)
        self.waits = []
        self.on_empty = on_empty

    def get(self, block=True, timeout=None):
        if self.items:
            return self.items.popleft()
        if block:
            self.waits.append(timeout)
        if self.on_empty and self.on_empty():
            return None
        raise queue.Empty

    def get_nowait(self):
        return self.get(block=False)


class SchedulingTests(unittest.TestCase):
    """覆盖历史故障的两个机制以及空闲、退出、断连等调度边界。"""
    def make_handler(self, incoming, sockets=('mic', 'file')):
        return WorkHandler(incoming, queue.Queue(), list(sockets), WorkerState())

    def test_backlog_does_not_wait_for_more_audio(self):
        incoming = ObservedQueue()
        handler = self.make_handler(incoming)
        # 500块对应10秒录音；排空阶段不能人为累计500×20ms的等待。
        for i in range(500):
            handler.buffer.enqueue(make_work(offset=i))
        for i in range(500):
            self.assertTrue(handler.drain_queue())
            self.assertEqual(handler.buffer.pop().offset, i)
        self.assertEqual(incoming.waits, [], '有积压时不得等待未来数据包')

    def test_single_recording_at_20ms_is_processed_before_release(self):
        class TimedQueue(ObservedQueue):
            """虚拟时钟按20ms交付一块；仅一条录音，无历史积压或并行任务。"""
            def __init__(self):
                super().__init__()
                self.now_ms = 0
                self.next_ms = 20
                self.count = 0

            def get(self, block=True, timeout=None):
                deadline = self.now_ms + (round(timeout * 1000) if block else 0)
                if self.count <= 500 and self.next_ms <= deadline:
                    self.now_ms = max(self.now_ms, self.next_ms)
                    work = make_work(offset=self.count, final=self.count == 500)
                    self.count += 1
                    self.next_ms += 20
                    return work
                self.now_ms = deadline
                raise queue.Empty

        incoming = TimedQueue()
        handler = self.make_handler(incoming)
        handled_at = []
        for i in range(501):
            self.assertTrue(handler.drain_queue())
            work = handler.buffer.pop()
            self.assertEqual(work.offset, i)
            handled_at.append(incoming.now_ms)
        self.assertLess(handled_at[0], 100, '不能等用户松手才开始消费首包')
        self.assertLessEqual(handled_at[-1] - 10020, 20, '松手后不能再等待一遍录音时长')

    def test_continuous_input_yields_to_processing(self):
        class ContinuousQueue(ObservedQueue):
            """模拟始终有下一块的输入；上限让旧实现确定失败而非挂死测试。"""
            def __init__(self):
                super().__init__()
                self.count = 0

            def get(self, block=True, timeout=None):
                self.count += 1
                if self.count > 256:
                    raise AssertionError('持续输入饿死了pipeline，收包必须有界')
                return make_work(offset=self.count)

        incoming = ContinuousQueue()
        handler = self.make_handler(incoming)
        self.assertTrue(handler.drain_queue())
        self.assertIsNotNone(handler.buffer.pop())
        self.assertLessEqual(incoming.count, 256)

    def test_consecutive_recordings_keep_arrival_order(self):
        buffer = WorkBuffer(WorkerState())
        works = [make_work('first', offset=0), make_work('first', offset=1, final=True),
                 make_work('second', offset=0), make_work('second', offset=1, final=True)]
        for work in works:
            buffer.enqueue(work)
        self.assertEqual([buffer.pop() for _ in works], works)
        self.assertTrue(buffer.is_empty)

    def test_sockets_rotate_without_reordering_their_packets(self):
        buffer = WorkBuffer(WorkerState())
        a = [make_work('a', 'mic', i) for i in range(3)]
        b = [make_work('b', 'file', i) for i in range(2)]
        for work in a + b:
            buffer.enqueue(work)
        self.assertEqual([buffer.pop() for _ in range(5)], [a[0], b[0], a[1], b[1], a[2]])

    def test_empty_socket_rejoins_at_tail(self):
        buffer = WorkBuffer(WorkerState())
        a, b, c = make_work('a'), make_work('b', 'file'), make_work('c')
        buffer.enqueue(a)
        self.assertIs(buffer.pop(), a)
        buffer.enqueue(b)
        buffer.enqueue(c)
        self.assertEqual([buffer.pop(), buffer.pop()], [b, c])

    def test_idle_wait_retains_engine_cleanup_and_shutdown(self):
        incoming = ObservedQueue()
        handler = self.make_handler(incoming)
        # 第一次空闲超时做维护，下一次拿到退出标记，不忙轮询。
        def cleanup():
            incoming.items.append(None)
        handler.cleanup_engines = Mock(side_effect=cleanup)
        self.assertFalse(handler.drain_queue())
        handler.cleanup_engines.assert_called_once()
        self.assertEqual(incoming.waits, [1])

    def test_disconnected_input_is_skipped(self):
        incoming = ObservedQueue([make_work('dead', 'gone'), make_work('live')])
        handler = self.make_handler(incoming)
        self.assertTrue(handler.drain_queue())
        self.assertEqual(handler.buffer.pop().task_id, 'live')
        self.assertNotIn('dead', handler.state.sessions)

    def test_cleanup_cancels_stale_runner_and_keeps_live_work(self):
        handler = self.make_handler(ObservedQueue())
        handler.pipeline = Mock()
        handler.buffer.enqueue(make_work('dead', 'gone'))
        live = make_work('live')
        handler.buffer.enqueue(live)
        handler.cleanup()
        handler.pipeline.cleanup_tasks.assert_called_once_with(['dead'])
        self.assertIs(handler.buffer.pop(), live)
        self.assertIsNone(handler.buffer.pop())

    def test_loop_returns_finals_in_recording_order_without_packet_delays(self):
        processed = []
        works = [make_work(task, offset=i, final=i == 50)
                 for task in ('first', 'second', 'third') for i in range(51)]
        incoming = ObservedQueue(works, on_empty=lambda: len(processed) == len(works))
        handler = self.make_handler(incoming)

        def process(work):
            processed.append(work)
            if work.is_final:
                return Result(work.task_id, work.socket_id, work.source, is_final=True)
            return None

        handler.pipeline = Mock(process=Mock(side_effect=process))
        handler.loop()
        self.assertEqual(processed, works, '音频与final必须按同一连接的到达顺序消费')
        self.assertEqual([handler.queue_out.get_nowait().task_id for _ in range(3)],
                         ['first', 'second', 'third'])
        self.assertFalse(handler.state.sessions)
        self.assertFalse([wait for wait in incoming.waits if wait != 1])


if __name__ == '__main__':
    unittest.main()
