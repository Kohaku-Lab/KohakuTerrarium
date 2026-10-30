"""Configured-input polling with serial turns and busy-safe command admission."""

import asyncio

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


async def drive_input(agent, *, startup: bool = False) -> None:
    """Poll configured input, admitting opted-in commands during foreground turns."""

    async def start() -> None:
        if startup:
            await agent._fire_startup_trigger()
            agent._startup_settled.set()

    if not getattr(agent.input, "supports_busy_commands", False):
        await start()
        await _read_inputs(agent, agent._process_event)
        return

    queue = asyncio.Queue()

    async def wait_until_idle() -> None:
        await queue.join()
        async with agent._processing_lock:
            pass

    async def enqueue(event) -> None:
        queue.put_nowait(event)

    async def read() -> None:
        await _read_inputs(agent, enqueue)
        queue.put_nowait(None)

    async def process() -> None:
        await start()
        while True:
            event = await queue.get()
            try:
                if event is None:
                    return
                if event.context.pop("deferred_input_command", False):
                    async with agent._processing_lock:
                        pass
                    result = await agent.input.run_deferred_command(event.content)
                    if result is not None:
                        if getattr(agent.input, "_exit_requested", False):
                            return
                        if result.consumed:
                            continue
                        if result.output:
                            event.content = result.output
                await agent._process_event(event)
            finally:
                queue.task_done()

    input_module = agent.input
    previous_waiter = input_module.command_idle_waiter
    input_module.command_idle_waiter = wait_until_idle
    processor = asyncio.create_task(process())
    reader = asyncio.create_task(read())
    tasks = [reader, processor]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        if tasks[0] in done:
            await tasks[1]
    finally:
        input_module.command_idle_waiter = previous_waiter
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _read_inputs(agent, dispatch) -> None:
    """Read events until shutdown and dispatch each accepted input."""
    while agent._running:
        event = await agent.input.get_input()
        if event is None:
            if getattr(agent.input, "exit_requested", False):
                logger.info("Exit requested")
                return
            await asyncio.sleep(0.01)
            continue
        logger.info(
            "Input received, processing event",
            event_type=event.type,
            content=f"{len(event.get_text_content())} chars",
        )
        await dispatch(event)
