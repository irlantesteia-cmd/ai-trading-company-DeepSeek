import logging
import sys

try:
    from pythonjsonlogger.json import JsonFormatter  # >= 3.0
except ImportError:  # < 3.0
    from pythonjsonlogger.jsonlogger import JsonFormatter  # type: ignore[no-redef]


def setup_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger()
    logger.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(handler)
    return logger