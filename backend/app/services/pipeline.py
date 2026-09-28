"""
Pipeline service aliases re-exporting core ingestion pipeline components.
"""

from app.core.pipeline import (
    KeyedMultilineAssembler,
    QueueConsumer,
    InternalLogHandler,
    IngestionRateTracker,
    get_queue,
    get_queue_consumer,
    set_queue_consumer,
    increment_dropped_count,
    get_dropped_count,
    increment_dropped_by_filter_count,
    get_dropped_by_filter_count,
    record_ingest,
    get_ingest_rate,
    reset_ingest_rate,
    clean_log_text,
    detect_severity,
)

__all__ = [
    "KeyedMultilineAssembler",
    "QueueConsumer",
    "InternalLogHandler",
    "IngestionRateTracker",
    "get_queue",
    "get_queue_consumer",
    "set_queue_consumer",
    "increment_dropped_count",
    "get_dropped_count",
    "increment_dropped_by_filter_count",
    "get_dropped_by_filter_count",
    "record_ingest",
    "get_ingest_rate",
    "reset_ingest_rate",
    "clean_log_text",
    "detect_severity",
]
