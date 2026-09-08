"""Independent first-observation bootstrap; regular collection belongs to Prefect."""
import logging
import os

from ..position_news.runtime import CollectionRuntime
from . import collector

logger = logging.getLogger(__name__)
_runtime = None


def _cycle():
    bootstrap = os.environ.get("WHALE_TRADE_EMBEDDED_BOOTSTRAP_ONLY", "true" if os.environ.get("RENDER") else "false")
    result = collector.run_collection_cycle(bootstrap_only=bootstrap.lower() not in {"0", "false", "no"})
    if result["collected_count"]:
        logger.info("whale activity cycle: %s", result)


def start():
    global _runtime
    if os.environ.get("WHALE_TRADE_EMBEDDED_ENABLED", "true").lower() in {"0", "false", "no"}:
        return
    if _runtime is None:
        _runtime = CollectionRuntime(cycle=_cycle, scan_seconds=5)
        _runtime.start()


def request_collection():
    if _runtime is not None:
        _runtime.wake()


async def stop():
    global _runtime
    current, _runtime = _runtime, None
    if current is not None:
        await current.stop()
