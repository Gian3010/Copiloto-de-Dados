"""Escolha automática de visualização a partir da forma do resultado."""
from __future__ import annotations

from typing import Optional

import pandas as pd

from .safety import QueryResult

_TIME_HINTS = ("mes", "month", "data", "date", "dia", "ano")


def to_frame(res: QueryResult) -> pd.DataFrame:
    return pd.DataFrame(res.rows, columns=res.columns)


def pick_chart(df: pd.DataFrame) -> Optional[str]:
    """Devolve 'line', 'bar' ou ``None`` (apenas tabela)."""
    if df.shape[1] < 2 or df.shape[0] < 2 or df.shape[0] > 60:
        return None
    first, numeric = df.columns[0], df.select_dtypes("number").columns
    if len(numeric) == 0 or first in numeric:
        return None
    return "line" if any(h in str(first).lower() for h in _TIME_HINTS) else "bar"


def chart_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Índice = 1ª coluna; séries = colunas numéricas."""
    numeric = [c for c in df.select_dtypes("number").columns if c != df.columns[0]]
    return df.set_index(df.columns[0])[numeric]
