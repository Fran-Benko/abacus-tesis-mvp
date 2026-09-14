"""
Logging estructurado con structlog + salida coloreada para consola.
INV-05: nunca se loggea execution_arguments crudo.
"""
import logging

import structlog


def setup_logging(level: str = "INFO") -> None:
    """Configura el sistema de logging estructurado con salida de consola."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str):
    """Retorna un logger structlog vinculado al nombre indicado."""
    return structlog.get_logger(name)
