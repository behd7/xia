"""M4 后台任务生命周期（docs/DESIGN.md §6.7/§6.9）。

两条循环都先 sleep 再工作，避免启动瞬间与 reconcile/首个请求抢 LLM。
循环体自行兜异常，单个 NPC 失败不会让任务静默死掉。
"""

import asyncio

from ..config import settings
from . import gossip as gossip_mod
from . import heart as heart_mod
from . import npc as npc_mod
from . import reflection as reflection_mod


async def _reflect_loop(db, store) -> None:
    while True:
        await asyncio.sleep(settings.scheduler_tick_real)
        try:
            for npc_id in npc_mod.ids():
                try:
                    await reflection_mod.run_once(db, store, npc_id)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    print(f"[scheduler] reflection npc {npc_id} failed: {e!r}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[scheduler] reflection tick failed: {e!r}")


async def _gossip_loop(db, store) -> None:
    while True:
        await asyncio.sleep(settings.gossip_interval_real)
        try:
            await gossip_mod.scan(db, store)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[scheduler] gossip tick failed: {e!r}")


async def _plan_loop(db, store) -> None:
    while True:
        await asyncio.sleep(settings.scheduler_tick_real)
        if not settings.plan_enabled:
            continue
        try:
            await heart_mod.ensure_all(db, store)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[scheduler] plan tick failed: {e!r}")


async def start(db, store) -> list[asyncio.Task]:
    if not settings.scheduler_enabled:
        return []
    return [
        asyncio.create_task(_reflect_loop(db, store)),
        asyncio.create_task(_gossip_loop(db, store)),
        asyncio.create_task(_plan_loop(db, store)),
    ]


async def stop(tasks: list[asyncio.Task]) -> None:
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
