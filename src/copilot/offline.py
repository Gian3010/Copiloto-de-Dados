"""Planejador offline (sem LLM) baseado em modelos de consulta.

Permite rodar e demonstrar o app sem chave de API. NÃO é um agente: reconhece um conjunto fixo de
perguntas por palavras-chave. O agente com LLM (agent.py) cobre perguntas abertas.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

REV = "SUM(oi.quantity * oi.unit_price)"
JOIN = "FROM orders o JOIN order_items oi ON oi.order_id = o.order_id"
OK = "o.status <> 'cancelado'"


@dataclass
class Plan:
    sql: str
    explanation: str


def _norm(q: str) -> str:
    q = "".join(c for c in unicodedata.normalize("NFD", q) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", q.lower())


TEMPLATES: list[tuple[tuple[str, ...], Plan]] = [
    (("receita", "mes"), Plan(
        f"SELECT strftime('%Y-%m', o.order_date) AS mes, ROUND({REV}, 2) AS receita {JOIN} WHERE {OK} GROUP BY mes ORDER BY mes",
        "Receita mensal (pedidos não cancelados).")),
    (("faturamento", "mes"), Plan(
        f"SELECT strftime('%Y-%m', o.order_date) AS mes, ROUND({REV}, 2) AS receita {JOIN} WHERE {OK} GROUP BY mes ORDER BY mes",
        "Faturamento mensal (pedidos não cancelados).")),
    (("top", "produtos"), Plan(
        f"SELECT p.name AS produto, ROUND({REV}, 2) AS receita FROM orders o JOIN order_items oi ON oi.order_id = o.order_id JOIN products p ON p.product_id = oi.product_id WHERE {OK} GROUP BY p.product_id ORDER BY receita DESC LIMIT 10",
        "10 produtos com maior receita.")),
    (("mais", "vendidos"), Plan(
        "SELECT p.name AS produto, SUM(oi.quantity) AS unidades FROM orders o JOIN order_items oi ON oi.order_id = o.order_id JOIN products p ON p.product_id = oi.product_id WHERE o.status <> 'cancelado' GROUP BY p.product_id ORDER BY unidades DESC LIMIT 10",
        "10 produtos mais vendidos em unidades.")),
    (("por", "estado"), Plan(
        f"SELECT c.state AS estado, ROUND({REV}, 2) AS receita FROM orders o JOIN order_items oi ON oi.order_id = o.order_id JOIN customers c ON c.customer_id = o.customer_id WHERE {OK} GROUP BY c.state ORDER BY receita DESC",
        "Receita por estado do cliente.")),
    (("por", "canal"), Plan(
        f"SELECT o.channel AS canal, COUNT(DISTINCT o.order_id) AS pedidos, ROUND({REV}, 2) AS receita {JOIN} WHERE {OK} GROUP BY o.channel ORDER BY receita DESC",
        "Pedidos e receita por canal de venda.")),
    (("ticket", "medio"), Plan(
        f"SELECT c.segment AS segmento, ROUND({REV} * 1.0 / COUNT(DISTINCT o.order_id), 2) AS ticket_medio FROM orders o JOIN order_items oi ON oi.order_id = o.order_id JOIN customers c ON c.customer_id = o.customer_id WHERE {OK} GROUP BY c.segment ORDER BY ticket_medio DESC",
        "Ticket médio por segmento de cliente.")),
    (("cancel",), Plan(
        "SELECT strftime('%Y-%m', order_date) AS mes, ROUND(100.0 * SUM(status = 'cancelado') / COUNT(*), 2) AS pct_cancelados FROM orders GROUP BY mes ORDER BY mes",
        "Taxa de cancelamento por mês (%).")),
    (("clientes", "novos"), Plan(
        "SELECT strftime('%Y-%m', signup_date) AS mes, COUNT(*) AS novos_clientes FROM customers GROUP BY mes ORDER BY mes",
        "Novos clientes cadastrados por mês.")),
    (("categoria",), Plan(
        f"SELECT p.category AS categoria, ROUND({REV}, 2) AS receita FROM orders o JOIN order_items oi ON oi.order_id = o.order_id JOIN products p ON p.product_id = oi.product_id WHERE {OK} GROUP BY p.category ORDER BY receita DESC",
        "Receita por categoria de produto.")),
]

SUPPORTED = [
    "Qual a receita por mês?", "Quais os top produtos?", "Quais os produtos mais vendidos?",
    "Qual a receita por estado?", "Pedidos e receita por canal", "Qual o ticket médio por segmento?",
    "Como está a taxa de cancelamento por mês?", "Quantos clientes novos por mês?",
    "Receita por categoria",
]


def plan_offline(question: str) -> Plan | None:
    q = _norm(question)
    for keys, plan in TEMPLATES:
        if all(k in q for k in keys):
            return plan
    return None
