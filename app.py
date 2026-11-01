"""Copiloto de Dados — app Streamlit.   Rodar:  streamlit run app.py"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from copilot.agent import AnthropicLLM, DataAgent, OfflineAgent  # noqa: E402
from copilot.audit import AuditLog  # noqa: E402
from copilot.charts import chart_frame, pick_chart, to_frame  # noqa: E402
from copilot.db import build_database  # noqa: E402
from copilot.offline import SUPPORTED  # noqa: E402

DB_PATH = Path(os.getenv("COPILOT_DB", ROOT / "data" / "aurora.db"))
AUDIT_PATH = Path(os.getenv("COPILOT_AUDIT_DB", ROOT / "data" / "audit.db"))

st.set_page_config(page_title="Copiloto de Dados · Mercado Aurora", page_icon="📊", layout="wide")


@st.cache_resource(show_spinner="Preparando o banco de dados sintético…")
def get_db() -> Path:
    if not DB_PATH.exists():
        build_database(DB_PATH)
    return DB_PATH


@st.cache_resource
def get_audit() -> AuditLog:
    return AuditLog(AUDIT_PATH)


def secret(name: str) -> str:
    """Lê de variável de ambiente ou de st.secrets (Streamlit Community Cloud)."""
    if os.getenv(name):
        return os.getenv(name, "")
    try:
        return str(st.secrets.get(name, ""))
    except Exception:  # noqa: BLE001 - sem secrets.toml
        return ""


def make_agent():
    db = get_db()
    key, model = secret("ANTHROPIC_API_KEY"), secret("COPILOT_MODEL")
    if key and model:
        os.environ["ANTHROPIC_API_KEY"] = key
        return DataAgent(db, AnthropicLLM(model)), "llm"
    return OfflineAgent(db), "offline"


agent, mode = make_agent()
audit = get_audit()

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("📊 Copiloto de Dados")
    st.caption("Mercado Aurora · e-commerce **fictício** com dados sintéticos")
    if mode == "llm":
        st.success("Modo agente (LLM) ativo")
    else:
        st.warning("Modo demonstração (sem LLM): só perguntas pré-definidas. Configure "
                   "`ANTHROPIC_API_KEY` e `COPILOT_MODEL` para perguntas livres.")
    st.subheader("Perguntas de exemplo")
    for s in (SUPPORTED if mode == "offline" else [
            "Qual a receita por mês e como está a tendência?",
            "Quais categorias mais cresceram no último trimestre?",
            "O ticket médio dos clientes premium é maior que o dos de varejo?",
            "Quais estados têm a maior taxa de cancelamento?",
            "Qual canal tem o melhor ticket médio?"]):
        if st.button(s, use_container_width=True, key=f"ex-{s}"):
            st.session_state["pending"] = s
    st.divider()
    stt = audit.stats()
    c1, c2 = st.columns(2)
    c1.metric("Perguntas", stt["perguntas"])
    c2.metric("Sucesso", f"{stt['taxa_sucesso']:.0%}")
    c1.metric("Latência média", f"{stt['latencia_media_ms'] / 1000:.1f}s")
    c2.metric("👍 / 👎", f"{stt['positivos']} / {stt['negativos']}")
    if st.button("Limpar conversa"):
        st.session_state["chat"] = []
        st.rerun()

# ------------------------------------------------------------------ chat
st.session_state.setdefault("chat", [])


def render_answer(entry: dict, idx: int) -> None:
    st.markdown(entry["text"])
    res = entry.get("result")
    if res is not None and res.rows:
        df = to_frame(res)
        kind = pick_chart(df)
        if kind:
            frame = chart_frame(df)
            (st.line_chart if kind == "line" else st.bar_chart)(frame)
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.download_button("⬇️ Baixar CSV", df.to_csv(index=False).encode("utf-8"),
                           file_name="resultado.csv", mime="text/csv", key=f"dl-{idx}")
        for note in res.notes:
            st.caption(note)
    if entry.get("queries"):
        with st.expander(f"🔍 Como cheguei nisso ({len(entry['queries'])} consulta(s) SQL)"):
            for q in entry["queries"]:
                st.caption(q["purpose"] or "consulta")
                st.code(q["sql"], language="sql")
                if q["error"]:
                    st.error(q["error"])
                else:
                    st.caption(f"{q['rows']} linhas · {q['ms']} ms")
    if entry.get("audit_id") and not entry.get("rated"):
        c1, c2, _ = st.columns([1, 1, 8])
        if c1.button("👍", key=f"up-{idx}"):
            audit.set_feedback(entry["audit_id"], True)
            entry["rated"] = True
            st.rerun()
        if c2.button("👎", key=f"down-{idx}"):
            audit.set_feedback(entry["audit_id"], False)
            entry["rated"] = True
            st.rerun()


st.header("Pergunte aos dados em português")
st.caption("O agente escreve SQL **somente leitura**, executa com travas de segurança e explica o resultado.")

for i, e in enumerate(st.session_state["chat"]):
    with st.chat_message(e["role"]):
        if e["role"] == "user":
            st.markdown(e["text"])
        else:
            render_answer(e, i)

question = st.chat_input("Ex.: qual a receita por mês?") or st.session_state.pop("pending", None)
if question:
    st.session_state["chat"].append({"role": "user", "text": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("Consultando os dados…"):
            ans = agent.ask(question)
        entry = {"role": "assistant", "text": ans.text, "result": ans.result, "queries": ans.queries,
                 "audit_id": audit.record(question, ans)}
        st.session_state["chat"].append(entry)
        render_answer(entry, len(st.session_state["chat"]) - 1)
