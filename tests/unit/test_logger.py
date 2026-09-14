"""Verifica que el logger de producción sea compatible con sus processors."""
from argentgob.observability.logger import get_logger, setup_logging


def test_setup_logging_permite_emitir_eventos():
    setup_logging()
    get_logger(__name__).info("logger_smoke_test", tool="stock_price")