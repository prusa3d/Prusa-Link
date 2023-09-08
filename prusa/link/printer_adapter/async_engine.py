import asyncio
import logging
from asyncio import Event
from time import monotonic

log = logging.getLogger(__name__)


class TaskItem:

    def __init__(self, handler, *args, **kwargs):
        self._handler = handler
        self._args = args
        self._kwargs = kwargs
        self._called_timestamp = None
        self._future = asyncio.Future()
        self._task = None

    @property
    def future(self):
        return self._future

    async def __call__(self):
        self._called_timestamp = monotonic()
        self._task = await asyncio.create_task(
            self._handler(*self._args, **self._kwargs))
        result = self._task.result()
        self._future.set_result(result)
        return self._task


class AsyncEngine:

    def __init__(self):
        self._asyncio_loop = None

        self._synchronous_queue = asyncio.Queue()
        self._quit_evt = Event()
        self._async_tasks = asyncio.Queue()
        self._sync_tasks = asyncio.Queue()

        self._loop_task = None
        self._clean_async_task = None
        self._clean_sync_task = None

    def do_sync(self, handler, *args, **kwargs):
        """Schedule a handler to run asynchronously"""
        item = TaskItem(handler, *args, **kwargs)
        asyncio.run_coroutine_threadsafe(
            self._synchronous_queue.put(item), self._asyncio_loop)
        return item.future

    def do_async(self, handler, *args, **kwargs):
        """Schedule a handler to run asynchronously"""
        item = TaskItem(handler, *args, **kwargs)
        asyncio.run_coroutine_threadsafe(
            self._do_async(item), self._asyncio_loop)
        return item.future

    async def _do_async(self, item):
        """Schedule a handler to run asynchronously"""
        task = await item()
        await self._async_tasks.put(task)

    async def _loop(self):
        self._asyncio_loop = asyncio.get_running_loop()
        while not self._quit_evt.is_set():
            item = await self._synchronous_queue.get()
            await self._sync_tasks.put(asyncio.create_task(item()))

    async def _clean_tasks(self, task_queue):
        while not self._quit_evt.is_set():
            task = await task_queue.get()
            if task is None:
                break
            try:
                await task
            except:
                log.exception("Exception in task")

    async def _clean_sync_tasks(self):
        while not self._quit_evt.is_set():
            task = await self._sync_tasks.get()
            await self._await_task(task)
            await task

    async def _clean_async_tasks(self):
        while not self._quit_evt.is_set():
            task = await self._async_tasks.get()
            await self._await_task(task)

    def run(self):
        """Starts the engine"""
        # Call from the main thread
        self._loop_task = asyncio.create_task(self._loop())
        self._clean_async_task = asyncio.create_task(
            self._clean_async_tasks())
        self._clean_sync_task = asyncio.create_task(
            self._clean_sync_tasks())

    async def stop(self):
        self._quit_evt.set()
        await self._sync_tasks.put(None)
        await self._async_tasks.put(None)
        await self._synchronous_queue.put(None)
