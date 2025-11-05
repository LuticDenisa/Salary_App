import logging
import structlog

def setup_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
    )

    logging.getLogger("werkzeug").setLevel(logging.INFO)

    logging.getLogger("werkzeug.access").setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S"), 
            structlog.dev.ConsoleRenderer(colors=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )

def get_logger(name: str | None = None):
    return structlog.get_logger(name or __name__)
