"""Execução segura de SQL gerado por LLM (nunca confie na saída do modelo).

Defesa em profundidade:
1. conexão ``mode=ro`` (somente leitura no nível do arquivo);
2. *authorizer* do SQLite: só permite SELECT/READ/FUNCTION (bloqueia ATTACH, PRAGMA, DDL, DML);
3. uma instrução por vez (o driver rejeita múltiplas);
4. limite de linhas e tempo máximo (progress handler);
5. tabelas internas (``sqlite_master`` etc.) são bloqueadas; o arquivo só contém ``ALLOWED_TABLES``.
"""
from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

ALLOWED_TABLES = {"customers", "products", "orders", "order_items"}
MAX_ROWS = 500
MAX_SECONDS = 5.0


class UnsafeSQLError(Exception):
    """SQL rejeitado pelas regras de segurança."""


class SQLExecutionError(Exception):
    """Erro do banco ao executar um SQL válido e permitido (ex.: coluna inexistente)."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool = False
    elapsed_ms: float = 0.0
    sql: str = ""
    notes: list[str] = field(default_factory=list)


def _authorizer(action: int, arg1, arg2, dbname, source):
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE):
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        # O banco de negócio só contém ALLOWED_TABLES (+ tabelas internas sqlite_*, que bloqueamos).
        # Nomes de CTE também chegam aqui como "tabela", por isso não usamos lista branca estrita.
        name = (arg1 or "").lower()
        return sqlite3.SQLITE_DENY if name.startswith("sqlite_") else sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def clean_sql(sql: str) -> str:
    """Remove cercas markdown e ``;`` finais; rejeita vazio e múltiplas instruções."""
    sql = sql.strip()
    sql = re.sub(r"^```(?:sql)?\s*|\s*```$", "", sql, flags=re.IGNORECASE).strip()
    sql = sql.rstrip(";").strip()
    if not sql:
        raise UnsafeSQLError("SQL vazio.")
    if ";" in sql:
        raise UnsafeSQLError("Apenas uma instrução SQL por vez.")
    if not re.match(r"^(select|with)\b", sql, flags=re.IGNORECASE):
        raise UnsafeSQLError("Apenas consultas SELECT (ou WITH ... SELECT) são permitidas.")
    return sql


def run_readonly(db_path: str | Path, sql: str, max_rows: int = MAX_ROWS,
                 max_seconds: float = MAX_SECONDS) -> QueryResult:
    sql = clean_sql(sql)
    uri = f"file:{Path(db_path).resolve()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    deadline = time.monotonic() + max_seconds
    con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    con.set_authorizer(_authorizer)
    t0 = time.perf_counter()
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(max_rows + 1)
    except sqlite3.DatabaseError as exc:
        msg = str(exc)
        if "authoriz" in msg or "prohibited" in msg:
            raise UnsafeSQLError("Operação ou tabela não permitida.") from exc
        if "interrupted" in msg:
            raise SQLExecutionError(f"Consulta excedeu {max_seconds:g}s e foi cancelada.") from exc
        raise SQLExecutionError(msg) from exc
    finally:
        con.close()
    truncated = len(rows) > max_rows
    return QueryResult(cols, rows[:max_rows], truncated, (time.perf_counter() - t0) * 1000, sql,
                       [f"Resultado limitado às primeiras {max_rows} linhas."] if truncated else [])
