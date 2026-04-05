"""
Lane-based command queue — OpenClaw Lane Strategy 的 Python 实现。

核心思想：
  每个命名车道（如 "user:+86xxx"、"crew"、"memory"）拥有独立的 asyncio.Queue
  和专属的 worker 协程，确保同一车道内的操作严格串行，不同车道间完全并行。
  这替代了传统的复杂锁机制，大幅降低执行步骤中的冲突率。

用法:
    queue = LaneQueue()
    result = await queue.submit("user:alice", my_async_fn, arg1, kw=val)

关闭:
    await queue.close()
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class _LaneJob:
    __slots__ = ("fn", "args", "kwargs", "fut")

    def __init__(
        self,
        fn: Callable[..., Awaitable[Any]],
        args: tuple,
        kwargs: dict,
        fut: asyncio.Future,
    ):
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.fut = fut


class LaneQueue:
    """
    按车道串行、跨车道并行的异步命令队列。

    lane_maxsize: 每条车道最多排队的任务数（0 = 无限）
    """

    def __init__(self, lane_maxsize: int = 0):
        self._queues: dict[str, asyncio.Queue] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._lane_maxsize = lane_maxsize
        self._closed = False

    def _get_or_create_lane(self, lane: str) -> asyncio.Queue:
        if lane not in self._queues:
            q: asyncio.Queue = asyncio.Queue(maxsize=self._lane_maxsize)
            self._queues[lane] = q
            self._workers[lane] = asyncio.create_task(
                self._worker(lane, q), name=f"lane:{lane}"
            )
        return self._queues[lane]

    async def _worker(self, lane: str, q: asyncio.Queue) -> None:
        """每条车道的专属消费者协程。"""
        while True:
            job: _LaneJob | None = await q.get()
            if job is None:  # None 是关闭哨兵
                q.task_done()
                break
            try:
                result = await job.fn(*job.args, **job.kwargs)
                if not job.fut.done():
                    job.fut.set_result(result)
            except Exception as exc:
                if not job.fut.done():
                    job.fut.set_exception(exc)
                logger.warning("Lane '%s' job failed: %s", lane, exc)
            finally:
                q.task_done()
        logger.debug("Lane worker '%s' exited", lane)

    async def submit(
        self,
        lane: str,
        fn: Callable[..., Awaitable[_T]],
        *args: Any,
        **kwargs: Any,
    ) -> _T:
        """
        将协程提交到指定车道并等待结果。
        同一 lane 内严格串行；不同 lane 间完全并行。
        """
        if self._closed:
            raise RuntimeError("LaneQueue 已关闭")
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        job = _LaneJob(fn, args, kwargs, fut)
        q = self._get_or_create_lane(lane)
        await q.put(job)
        return await fut  # type: ignore[return-value]

    async def close(self) -> None:
        """向所有车道发送关闭哨兵并等待 worker 退出。"""
        self._closed = True
        for q in self._queues.values():
            await q.put(None)
        for task in self._workers.values():
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
        self._queues.clear()
        self._workers.clear()

    @property
    def active_lanes(self) -> list[str]:
        return list(self._queues.keys())

    @property
    def is_closed(self) -> bool:
        return self._closed


# 进程级单例，延迟初始化
_global_lane_queue: LaneQueue | None = None


def get_lane_queue() -> LaneQueue:
    global _global_lane_queue
    if _global_lane_queue is None:
        _global_lane_queue = LaneQueue()
    return _global_lane_queue


async def close_lane_queue() -> None:
    global _global_lane_queue
    if _global_lane_queue is not None:
        await _global_lane_queue.close()
        _global_lane_queue = None
