"""Log de auditoria das perguntas (quem perguntou o quê, qual SQL rodou, quanto custou).

Em produção isto iria para Postgres/BigQuery. Aqui usa um SQLite separado e gravável, pois o banco
de negócio é aberto em modo somente leitura.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .agent import AgentAnswer

DDL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, question TEXT NOT NULL, mode TEXT NOT NULL, ok INTEGER NOT NULL,
    n_queries INTEGER NOT NULL, sql_json TEXT NOT NULL, elapsed_ms REAL NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
    feedback INTEGER
)"""


class AuditLog:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._con() as con:
            con.execute(DDL)

    def _con(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def record(self, question: str, ans: AgentAnswer) -> int:
        with self._con() as con:
            cur = con.execute(
                "INSERT INTO audit_log (ts, question, mode, ok, n_queries, sql_json, elapsed_ms, "
                "input_tokens, output_tokens) VALUES (?,?,?,?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), question[:2000], ans.mode,
                 int(ans.ok), len(ans.queries), json.dumps([q["sql"] for q in ans.queries], ensure_ascii=False),
                 ans.elapsed_ms, ans.input_tokens, ans.output_tokens))
            return cur.lastrowid

    def set_feedback(self, row_id: int, positive: bool) -> None:
        with self._con() as con:
            con.execute("UPDATE audit_log SET feedback = ? WHERE id = ?", (int(positive), row_id))

    def stats(self) -> dict:
        with self._con() as con:
            n, ok, avg_ms, up, down = con.execute(
                "SELECT COUNT(*), COALESCE(SUM(ok),0), COALESCE(AVG(elapsed_ms),0), "
                "COALESCE(SUM(feedback = 1),0), COALESCE(SUM(feedback = 0),0) FROM audit_log").fetchone()
        return {"perguntas": n, "taxa_sucesso": (ok / n) if n else 0.0, "latencia_media_ms": avg_ms,
                "positivos": up, "negativos": down}
