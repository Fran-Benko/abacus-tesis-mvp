"""Entrypoint mínimo para AppTest de la vista Finanzas en vivo (AV-03).

`AppTest.from_file` ejecuta el script tal cual; la vista real solo define
`render()`, así que este wrapper la invoca como lo hace `dashboard.py`.
"""
from __future__ import annotations

from argentgob.dashboard.pages import live_finance

live_finance.render()
