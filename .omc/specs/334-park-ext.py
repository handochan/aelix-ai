"""Round-2 probe extension: one handler that parks until it is cancelled.

PARK_EVENT picks the hook ("input" or "before_agent_start"); PARK_MARK is a
file written on entry, so the driver types Ctrl+C only once the handler is
provably parked. The sleep is far longer than any measurement, so the handler
never returns by itself.
"""

import asyncio
import os

EVENT = os.environ["PARK_EVENT"]
MARK = os.environ["PARK_MARK"]


def setup(aelix):
    async def park(event, ctx):
        with open(MARK, "w") as fh:
            fh.write(f"entered {EVENT}\n")
        await asyncio.sleep(600)
        return None

    aelix.on(EVENT, park)
