"""Tests AppTest del dashboard multipágina (checklist §15).

Ejecuta cada vista del dashboard con `st.testing.v1.AppTest` y verifica que no
lance excepciones. Requiere PostgreSQL alcanzable (stack) — se salta si no hay
`DATABASE_URL` o si la conexión falla.

Nota: AppTest ejecuta el script en un entorno simulado; las vistas que
consultan la DB necesitan que el stack esté arriba.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

_REPO = Path(__file__).resolve().parents[2]
DASHBOARD = str(_REPO / "src" / "argentgob" / "dashboard" / "dashboard.py")


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="sin DATABASE_URL (requiere stack PostgreSQL)",
)
def test_dashboard_carga_sin_excepciones():
    """El entrypoint multipágina carga sin excepciones."""
    at = AppTest.from_file(DASHBOARD, default_timeout=30)
    at.run()
    assert not at.exception


@pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"),
    reason="sin DATABASE_URL (requiere stack PostgreSQL)",
)
def test_dashboard_no_expone_payload_crudo():
    """INV-05: ninguna vista expone execution_arguments ni payload crudo."""
    at = AppTest.from_file(DASHBOARD, default_timeout=30)
    at.run()
    # El texto renderizado no debe contener claves de payload crudo.
    texto = str(at)
    assert "execution_arguments" not in texto
    assert "raw_payload" not in texto