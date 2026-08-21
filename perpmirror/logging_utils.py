from __future__ import annotations

import logging
import re
from pathlib import Path


class SecretRedactionFilter(logging.Filter):
    _patterns = (
        re.compile(r"(?i)(api[_-]?key|secret|passphrase|signature|authorization)([=:]\s*)([^\s&,}]+)"),
        re.compile(r"(?i)(X-MBX-APIKEY|OK-ACCESS-(?:KEY|SIGN|PASSPHRASE))(['\" ]*[:=]['\" ]*)([^\s,'\"}]+)"),
        re.compile(r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9_-]+"),
    )

    @classmethod
    def redact(cls, value: object) -> str:
        text = str(value)
        for pattern in cls._patterns:
            text = pattern.sub(lambda match: f"{match.group(1)}{match.group(2)}***", text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        # Render with the original argument types first. Converting every argument to
        # str breaks third-party format strings such as httpx's HTTP status `%d`.
        record.msg = self.redact(record.getMessage())
        record.args = ()
        return True


def configure_logging(level: str = "INFO", log_dir: str | Path = "logs") -> None:
    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handlers: list[logging.Handler] = [
        logging.StreamHandler(),
        logging.FileHandler(directory / "perpmirror.log"),
    ]
    redactor = SecretRedactionFilter()
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(redactor)
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), handlers=handlers, force=True)
