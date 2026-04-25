from __future__ import annotations

import json
import logging

from vidsearch.logging_utils import JsonFormatter, log_event


def test_json_formatter_emits_extra_fields():
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="vidsearch.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.event = "demo_event"
    record.image_id = "img_123"
    payload = json.loads(formatter.format(record))

    assert payload["message"] == "hello"
    assert payload["event"] == "demo_event"
    assert payload["image_id"] == "img_123"
    assert payload["level"] == "INFO"


def test_log_event_attaches_structured_fields(caplog):
    logger = logging.getLogger("vidsearch.test")
    with caplog.at_level(logging.INFO):
        log_event(logger, logging.INFO, "ingest_image_complete", image_id="img_123", has_caption=True)

    record = caplog.records[-1]
    assert record.event == "ingest_image_complete"
    assert record.image_id == "img_123"
    assert record.has_caption is True
