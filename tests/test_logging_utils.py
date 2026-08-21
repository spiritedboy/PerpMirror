import logging

from perpmirror.logging_utils import SecretRedactionFilter


def test_redaction_preserves_percent_d_numeric_arguments() -> None:
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='HTTP Request: %s "%s %d %s"',
        args=("GET", "HTTP/1.1", 200, "OK"),
        exc_info=None,
    )
    assert SecretRedactionFilter().filter(record) is True
    assert record.getMessage() == 'HTTP Request: GET "HTTP/1.1 200 OK"'


def test_redaction_filters_rendered_url_without_changing_format_types() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="request=%s status=%d",
        args=("https://example.invalid?signature=visible", 200),
        exc_info=None,
    )
    SecretRedactionFilter().filter(record)
    rendered = record.getMessage()
    assert "visible" not in rendered
    assert "status=200" in rendered
