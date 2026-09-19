"""Tokens opacos de un solo uso para el resume de ejecuciones gobernadas.

Unidad 1 (hold) y Unidad 4 (resume/capability): el resume se autoriza con un
token opaco. La norma exige no persistir el token plano; solo se guarda su
hash SHA-256. El valor plano viaja únicamente en la respuesta al notificador /
resolución y nunca se escribe en la base de datos.
"""
import hashlib
import secrets


def generate_bearer_token() -> str:
    """Genera un token opaco aleatorio (URL-safe, ~43 caracteres).

    Se usa un único secreto de 32 bytes con secrets.token_urlsafe: aporta ~256
    bits de entropía, irreversible por fuerza bruta en la práctica.
    """
    return secrets.token_urlsafe(32)


def hash_token(presented_token: str) -> str:
    """Devuelve el hash SHA-256 del token en formato `sha256:<hex>`."""
    return "sha256:" + hashlib.sha256(presented_token.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    """Compara dos strings en tiempo constante (evita timing attacks)."""
    return secrets.compare_digest(left, right)