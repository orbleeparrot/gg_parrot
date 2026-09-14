"""Small public projections only. DB workers invalidate after committed writes."""
from .cache_runtime import ResponseCache

responses = ResponseCache("public_news", max_entries=256, max_bytes=8_000_000,
                          retry_seconds=2, max_retry_seconds=10)
