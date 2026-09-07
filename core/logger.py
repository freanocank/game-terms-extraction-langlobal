import logging
from pathlib import Path

from config import LOGGING_CONFIG


class RedactingFormatter(logging.Formatter):
    def __init__(self, fmt, secrets):
        super().__init__(fmt)
        self.secrets = tuple(secret for secret in secrets if secret)

    def format(self, record):
        text = super().format(record)
        for secret in self.secrets:
            text = text.replace(secret, "***")
        return text


def setup_logging(log_dir: str = "output", log_level: str = "INFO", secrets=()) -> logging.Logger:
    log_dir_path = Path(log_dir)
    log_dir_path.mkdir(parents=True, exist_ok=True)
    log_file = log_dir_path / "pipeline.log"

    level = getattr(logging, LOGGING_CONFIG.get("level", log_level).upper(), logging.INFO)
    fmt = LOGGING_CONFIG.get("format", "%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    handlers = []
    if LOGGING_CONFIG.get("file_enabled", True):
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    if LOGGING_CONFIG.get("console_enabled", True):
        handlers.append(logging.StreamHandler())

    for handler in handlers:
        handler.setFormatter(RedactingFormatter(fmt, secrets))
    logging.basicConfig(level=level, handlers=handlers, force=True)
    logger = logging.getLogger("pipeline")
    logger.info(f"Log level: {logging.getLevelName(level)}")
    return logger
