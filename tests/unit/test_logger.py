"""Verifica que el logger de producción sea compatible con sus processors."""
import logging

from argentgob.observability.logger import get_logger, setup_logging


def test_setup_logging_permite_emitir_eventos(caplog):
    setup_logging()
    get_logger(__name__).info("logger_smoke_test", tool="stock_price")
    assert any("logger_smoke_test" in r.message for r in caplog.records)


def test_setup_logging_configura_nivel_del_root_logger():
    # El LoggerFactory de structlog delega al módulo logging estándar, que
    # filtra por el nivel del root logger. setup_logging debe alinearlo para
    # que los eventos INFO no se descarten silenciosamente.
    setup_logging("INFO")
    assert logging.getLogger().level == logging.INFO
