"""
Tests del Sanitizer (Módulo B).

Verifican truncado de strings largos, enmascarado de datos sensibles y la
inmutabilidad del objeto original (INV-04/INV-16).
"""
import pytest

from argentgob.core.config import Settings
from argentgob.module_b.sanitizer import Sanitizer


@pytest.fixture
def sanitizer():
    return Sanitizer(Settings(max_string_bytes=4096))


def test_string_corto_sin_patrones_pasa_igual(sanitizer):
    """Un string corto y sin datos sensibles no se modifica."""
    out = sanitizer.sanitize({"query": "precio de AAPL"})
    assert out == {"query": "precio de AAPL"}


def test_string_largo_se_trunca(sanitizer):
    """Strings que superan max_string_bytes se reemplazan por un descriptor."""
    largo = "x" * 5000
    out = sanitizer.sanitize({"data": largo})
    assert out["data"] == "[TRUNCATED:5000bytes]"


def test_email_se_redacta(sanitizer):
    out = sanitizer.sanitize({"contacto": "escribime a juan.perez@gmail.com ya"})
    assert out["contacto"].startswith("[REDACTED:EMAIL:")


def test_numero_de_pago_se_redacta(sanitizer):
    out = sanitizer.sanitize({"pago": "tarjeta 4111111111111111 vencida"})
    assert out["pago"].startswith("[REDACTED:PAYMENT_NUMBER:")


def test_dni_se_redacta(sanitizer):
    out = sanitizer.sanitize({"doc": "mi dni es 30123456 gracias"})
    assert out["doc"].startswith("[REDACTED:POSSIBLE_DNI:")


def test_secret_se_redacta(sanitizer):
    out = sanitizer.sanitize({"conf": "api_key=SUPERSECRETO123"})
    assert out["conf"].startswith("[REDACTED:SECRET:")


def test_original_no_se_modifica(sanitizer):
    """INV-04: el objeto de entrada nunca se muta; se devuelve una copia nueva."""
    original = {"contacto": "juan.perez@gmail.com"}
    copia_referencia = dict(original)
    out = sanitizer.sanitize(original)
    assert original == copia_referencia  # entrada intacta
    assert out is not original  # copia nueva


def test_estructuras_anidadas_se_sanitizan(sanitizer):
    """La sanitización recorre dicts y listas anidados."""
    out = sanitizer.sanitize(
        {"nivel": {"emails": ["a@b.com", "texto normal"]}}
    )
    assert out["nivel"]["emails"][0].startswith("[REDACTED:EMAIL:")
    assert out["nivel"]["emails"][1] == "texto normal"


def test_valores_no_string_se_conservan(sanitizer):
    """Números y booleanos se conservan tal cual."""
    out = sanitizer.sanitize({"cantidad": 42, "activo": True})
    assert out == {"cantidad": 42, "activo": True}
