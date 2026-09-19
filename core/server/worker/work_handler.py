# coding: utf-8
"""
识别工作单元处理器

负责监听工作单元队列、执行识别流水线并将结果返回主进程。

公平调度：从不同客户端（socket）轮转取工作单元处理，防止文件转录淹没队列。
同 socket 内保持 FIFO 顺序，跨 socket 间轮转调度。
"""

from collections import OrderedDict, deque
from multiprocessing import Queue
from multiprocessing.managers import ListProxy
import queue
from .work_pipeline import WorkPipeline
from ..state import WorkerState
from . import logger


class WorkBuffer:
    """同连接保持接收顺序，跨连接轮转，避免后录的句子抢在前句之前返回。"""
    def __init__(self, state: WorkerState):
        self.state = state
        self._buffers: OrderedDict[str, deque] = OrderedDict()

    def enqueue(self, work):
        """以socket分组而非task分组，保留连续录音之间的final先后关系。"""
        sid = work.socket_id
        if sid not in self._buffers:
            self._buffers[sid] = deque()
        self.state.get_session(work.task_id, sid, work.source)
        self._buffers[sid].append(work)

    def pop(self):
        """轮到的连接只消费一块，再移到队尾；该连接内部始终FIFO。"""
        if not self._buffers:
            return None

        sid, buf = self._buffers.popitem(last=False)
        work = buf.popleft()

        if buf:
            self._buffers[sid] = buf

        return work

    def cleanup_works(self):
        """清理已断开连接 session 的缓冲工作单元。"""
        for sid, buf in list(self._buffers.items()):
            # 一个连接可以包含多次录音；只移除失效session的工作单元，不能把
            # task_id当socket_id查找，也不能因一条结束而误清同连接的下一条。
            live = deque(work for work in buf if work.task_id in self.state.sessions)
            if live:
                self._buffers[sid] = live
            else:
                del self._buffers[sid]

    @property
    def is_empty(self) -> bool:
        return len(self._buffers) == 0


class WorkHandler:
    """
    工作单元处理器

    协调输入输出队列与识别引擎之间的工作单元流。
    支持跨 socket 公平轮转调度。
    """
    # 连续输入不能占满整个循环；有限批量后必须让pipeline处理已有数据。
    MAX_DRAIN_ITEMS = 64

    def __init__(self, queue_in: Queue, queue_out: Queue, sockets_id: ListProxy, state: WorkerState):
        self.queue_in = queue_in
        self.queue_out = queue_out
        self.sockets_id = sockets_id
        self.state = state

        self.recognizer = None
        self.punc_model = None
        self.aligner = None
        self.pipeline = None

        self.buffer = WorkBuffer(state)

    def set_engine(self, recognizer, punc_model=None, aligner=None):
        """注入识别引擎实例并初始化管线"""
        self.recognizer = recognizer
        self.punc_model = punc_model
        self.aligner = aligner
        if getattr(recognizer, 'uses_task_runner', False):
            # qwen_asr_mlx 的语义切分和拼接已经迁入 package Runner；
            # Worker 这里只做音频增量转发和 final 结果格式化。
            from .qwen_mlx_runner_pipeline import QwenMLXRunnerPipeline
            self.pipeline = QwenMLXRunnerPipeline(recognizer, punc_model, aligner, self.state)
        else:
            self.pipeline = WorkPipeline(recognizer, punc_model, aligner, self.state)

    def drain_queue(self) -> bool:
        """有积压时仅收取当前可读的一批；只有完全空闲才阻塞等待首包。"""
        received = 0
        while received < self.MAX_DRAIN_ITEMS:
            try:
                if self.buffer.is_empty:
                    work = self.queue_in.get(timeout=1)
                else:
                    # 20ms音频流会让旧timeout=0.02不断收到新包而不消费，
                    # 松手后又逐包等20ms。积压时必须立刻消费，不能等未来包。
                    # multiprocessing.Queue的feeder尚未交付时可能暂时Empty；
                    # 下一轮自然重试，不使用不可靠的empty()/qsize()判定。
                    work = self.queue_in.get_nowait()
            except queue.Empty:
                if self.buffer.is_empty:
                    self.cleanup_engines()
                    continue
                else:
                    return True
            except InterruptedError:
                continue

            # 判断退出信号
            if work is None:
                return False

            # 断连包同样占用本轮预算，防止大量失效输入阻塞已有有效工作。
            received += 1

            # 跳过已断开连接客户端的工作单元
            if work.socket_id not in self.sockets_id:
                logger.debug(f"跳过断连客户端工作单元: {work.task_id[:8]}")
                continue

            # 工作单元进入缓冲区
            self.buffer.enqueue(work)

        return True

    def cleanup(self):
        """清理断连 socket 的缓冲工作单元和 session。"""
        stale_task_ids = [
            tid for tid, session in list(self.state.sessions.items())
            if session.result.socket_id not in self.sockets_id
        ]
        if stale_task_ids and self.pipeline and hasattr(self.pipeline, 'cleanup_tasks'):
            # Runner 内部也持有按 task_id 聚合的音频缓冲；session 清理时必须同步释放，
            # 否则客户端断连会把未 final 的长音频留在 worker 进程内。
            self.pipeline.cleanup_tasks(stale_task_ids)
        if self.state.cleanup_sessions(self.sockets_id):
            # 只有实际断连才扫描包队列；正常逐包消费无需反复复制全部积压数据，
            # 否则较长文件传输会把清理本身变成二次方开销。
            self.buffer.cleanup_works()

    def cleanup_engines(self):
        """时间戳引擎空闲时自动卸载。"""
        if self.pipeline and self.pipeline.aligner:
            self.pipeline.aligner.check_idle()

    def loop(self):
        """核心工作循环：drain 队列 → 清理断连 → 轮转执行一个工作单元。"""
        logger.info("WorkHandler 开始工作循环 (公平调度)")

        while True:
            try:
                if not self.drain_queue():
                    break

                work = self.buffer.pop()
                if work is None:
                    continue

                # 安全网：在 pipeline 处理前再次检查（工作单元可能在 drain→pop 之间成为孤儿）
                # if work.socket_id not in self.sockets_id:
                #     logger.debug(f"跳过断连客户端工作单元(安全网): {work.task_id[:8]}")
                #     self.cleanup()
                #     continue

                result = self.pipeline.process(work)
                if result is None:
                    self.cleanup()
                    continue

                self.queue_out.put(result)
                if result.is_final:
                    self.state.sessions.pop(work.task_id, None)
                self.cleanup()
            except InterruptedError:
                continue
            except Exception as e:
                logger.error(f"工作单元执行出错: {str(e)}", exc_info=True)

        logger.info("WorkHandler 工作循环结束")
