"""The start-up heap leaves the cyclic collector's scans (#277, D5-GC).

A full (generation 2) collection scans every tracked object. Most of the
server's heap is built once at start-up (content, world, zone instances,
registries) and lives for the whole run, so each full collection re-walks
it. `gc.freeze()` moves everything tracked at that moment into the
permanent generation, which no collection scans; later objects are
collected as before. Reference counting still frees frozen objects. Only
cyclic garbage among them is never collected (docs/ops/server-core.md).
"""

from __future__ import annotations

import gc


def freeze_startup_heap() -> int:
    """Collect once, then freeze what survives. Returns the frozen object count."""
    gc.collect()
    gc.freeze()
    return gc.get_freeze_count()
