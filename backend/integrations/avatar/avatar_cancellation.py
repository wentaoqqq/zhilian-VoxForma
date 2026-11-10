"""Cancellation bridge to the existing request-scoped inference cancellation."""
import asyncio
from pathlib import Path

from fastapi import HTTPException


async def speech_until_cancelled(request, request_path, operation):
    task = asyncio.create_task(operation)
    flag = Path(request_path).parent / '.cancel'
    try:
        while not task.done():
            if flag.exists() or await request.is_disconnected():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                # The GPU pool owns native-thread finalization. Its lease is
                # retained by the queue until clear_voice confirms cleanup.
                raise HTTPException(409, 'Avatar speech was cancelled')
            await asyncio.wait([task], timeout=0.2)
        return await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
