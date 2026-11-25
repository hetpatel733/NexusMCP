# backend app
import asyncio
import copy
import inspect
import uuid

# Workaround for Python 3.16 alpha C-extension UUID deepcopy crash
copy._deepcopy_dispatch[uuid.UUID] = lambda x, memo: x

# Workaround for Python 3.16 removal of deprecated asyncio.iscoroutinefunction
if not hasattr(asyncio, "iscoroutinefunction"):
    asyncio.iscoroutinefunction = inspect.iscoroutinefunction  # type: ignore
