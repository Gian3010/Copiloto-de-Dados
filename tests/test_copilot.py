import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from copilot.agent import AgentAnswer, DataAgent, OfflineAgent, Step, ToolCall  # noqa: E402
from copilot.audit import AuditLog  # noqa: E402
from copilot.charts import chart_frame, pick_chart, to_frame  # noqa: E402
from copilot.db import build_database  # noqa: E402
from copilot.offline import SUPPORTED, TEMPLATES, plan_offline  # noqa: E402
from copilot.safety import SQLExecutionError, UnsafeSQLError, clean_sql, run_readonly  # noqa: E402


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    return build_database(tmp_path_factory.mktemp("db") / "t.db", seed=1, n_customers=300, n_orders=2000)


# ------------------------------------------------------------ banco
def test_database_is_deterministic(tmp_path):
    a = build_database(tmp_path / "a.db", seed=5, n_customers=50, n_orders=200)
    b = build_database(tmp_path / "b.db", seed=5, n_customers=50, n_orders=200)
    q = "SELECT SUM(quantity * unit_price), COUNT(*) FROM order_items"
    assert sqlite3.connect(a).execute(q).fetchone() == sqlite3.connect(b).execute(q).fetchone()


def test_database_integrity(db):
    con = sqlite3.connect(db)
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert con.execute("SELECT COUNT(*) FROM orders WHERE status = 'cancelado'").fetchone()[0] > 0
    # nenhum pedido antes do cadastro do cliente
    assert con.execute("SELECT COUNT(*) FROM orders o JOIN customers c USING (customer_id) "
                       "WHERE o.order_date < c.signup_date").fetchone()[0] == 0


# ------------------------------------------------------------ segurança
@pytest.mark.parametrize("sql", [
    "DROP TABLE customers", "DELETE FROM orders", "UPDATE orders SET status='x'",
    "INSERT INTO customers VALUES (1,'a','SP','varejo','2025-01-01')",
    "SELECT 1; DROP TABLE orders", "PRAGMA table_info(orders)", "ATTACH DATABASE 'x.db' AS x",
    "CREATE TABLE t(a)", "   ", "",
])
def test_unsafe_sql_is_rejected(db, sql):
    with pytest.raises(UnsafeSQLError):
        run_readonly(db, sql)


def test_cannot_read_sqlite_master(db):
    with pytest.raises(UnsafeSQLError):
        run_readonly(db, "SELECT name FROM sqlite_master")


def test_write_blocked_even_inside_cte(db):
    with pytest.raises((UnsafeSQLError, SQLExecutionError)):
        run_readonly(db, "WITH x AS (SELECT 1) DELETE FROM orders")
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 2000


def test_select_works_and_row_limit(db):
    r = run_readonly(db, "```sql\nSELECT * FROM order_items;\n```", max_rows=10)
    assert len(r.rows) == 10 and r.truncated and r.notes


def test_sql_error_is_execution_error(db):
    with pytest.raises(SQLExecutionError):
        run_readonly(db, "SELECT coluna_inexistente FROM orders")


def test_runaway_query_is_cancelled(db):
    heavy = ("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x < 100000000) "
             "SELECT COUNT(*) FROM c")
    with pytest.raises(SQLExecutionError, match="excedeu"):
        run_readonly(db, heavy, max_seconds=0.3)


def test_clean_sql_requires_select():
    assert clean_sql("select 1;") == "select 1"
    with pytest.raises(UnsafeSQLError):
        clean_sql("VACUUM")


# ------------------------------------------------------------ offline
def test_every_supported_question_has_a_plan_and_runs(db):
    for q in SUPPORTED:
        plan = plan_offline(q)
        assert plan is not None, q
        assert run_readonly(db, plan.sql).rows, q
    assert len(TEMPLATES) >= len(SUPPORTED) - 1


def test_offline_agent_unknown_question(db):
    a = OfflineAgent(db).ask("qual a previsão do tempo?")
    assert not a.ok and a.mode == "offline" and "modo demonstração" in a.text


def test_revenue_template_matches_manual_sql(db):
    manual = sqlite3.connect(db).execute(
        "SELECT ROUND(SUM(oi.quantity*oi.unit_price),2) FROM orders o JOIN order_items oi USING(order_id) "
        "WHERE o.status <> 'cancelado'").fetchone()[0]
    res = OfflineAgent(db).ask("receita por mes").result
    assert round(sum(r[1] for r in res.rows), 2) == pytest.approx(manual, abs=1.0)


# ------------------------------------------------------------ agente (LLM falso)
class ScriptedLLM:
    """Responde com passos pré-definidos e guarda o que recebeu (para asserts)."""

    def __init__(self, steps):
        self.steps, self.seen = list(steps), []

    def step(self, system, messages, tools):
        self.seen.append((system, [dict(m) for m in messages]))
        return self.steps.pop(0)


def tool_step(sql, purpose="p", id="t1"):
    return Step("", [ToolCall(id, "run_sql", {"sql": sql, "purpose": purpose})],
                [{"type": "tool_use", "id": id, "name": "run_sql", "input": {"sql": sql, "purpose": purpose}}])


def text_step(t):
    return Step(t, [], [{"type": "text", "text": t}])


def test_agent_happy_path(db):
    llm = ScriptedLLM([tool_step("SELECT COUNT(*) AS n FROM customers"), text_step("Há 300 clientes.")])
    a = DataAgent(db, llm).ask("Quantos clientes?")
    assert a.ok and a.text == "Há 300 clientes." and a.result.rows == [(300,)]
    assert a.queries[0]["ok"] and "customers" in llm.seen[0][0]  # schema no system prompt
    last = llm.seen[1][1][-1]["content"][0]  # tool_result devolvido ao modelo
    assert last["type"] == "tool_result" and "300" in last["content"]


def test_agent_recovers_from_sql_error(db):
    llm = ScriptedLLM([tool_step("SELECT x FROM nada", id="a"), tool_step("SELECT COUNT(*) FROM orders", id="b"),
                       text_step("2000 pedidos.")])
    a = DataAgent(db, llm).ask("Quantos pedidos?")
    assert a.ok and [q["ok"] for q in a.queries] == [False, True]
    assert llm.seen[1][1][-1]["content"][0]["is_error"] is True


def test_agent_blocks_malicious_sql_from_model(db):
    llm = ScriptedLLM([tool_step("DROP TABLE orders"), text_step("Não posso fazer isso.")])
    a = DataAgent(db, llm).ask("apague tudo")
    assert a.queries[0]["error"] and sqlite3.connect(db).execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 2000


def test_agent_step_limit(db):
    llm = ScriptedLLM([tool_step("SELECT 1", id=str(i)) for i in range(10)])
    a = DataAgent(db, llm, max_steps=3).ask("loop")
    assert not a.ok and "limite" in a.text and len(a.queries) == 3


def test_agent_unknown_tool(db):
    bad = Step("", [ToolCall("z", "rm_rf", {})], [{"type": "tool_use", "id": "z", "name": "rm_rf", "input": {}}])
    llm = ScriptedLLM([bad, text_step("ok")])
    DataAgent(db, llm).ask("x")
    assert llm.seen[1][1][-1]["content"][0]["is_error"] is True


# ------------------------------------------------------------ auditoria e gráficos
def test_audit_log(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    rid = log.record("pergunta", AgentAnswer("r", queries=[{"sql": "SELECT 1"}], elapsed_ms=120.0))
    log.set_feedback(rid, True)
    s = log.stats()
    assert s["perguntas"] == 1 and s["taxa_sucesso"] == 1.0 and s["positivos"] == 1


def test_chart_selection():
    ts = pd.DataFrame({"mes": ["2026-01", "2026-02", "2026-03"], "receita": [1.0, 2.0, 3.0]})
    cat = pd.DataFrame({"categoria": ["a", "b"], "receita": [1.0, 2.0]})
    assert pick_chart(ts) == "line" and pick_chart(cat) == "bar"
    assert pick_chart(pd.DataFrame({"n": [1]})) is None
    assert list(chart_frame(ts).columns) == ["receita"]


def test_streamlit_app_smoke(tmp_path, monkeypatch):
    """Executa o app de verdade (headless) no modo offline e faz uma pergunta."""
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("COPILOT_DB", str(build_database(tmp_path / "app.db", seed=2, n_customers=100, n_orders=500)))
    monkeypatch.setenv("COPILOT_AUDIT_DB", str(tmp_path / "audit.db"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("COPILOT_MODEL", raising=False)
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=60).run()
    assert not at.exception
    at.chat_input[0].set_value("qual a receita por mes?").run()
    assert not at.exception
    assert any("Receita mensal" in m.value for m in at.markdown)
