"""Banco SQLite sintético de um e-commerce fictício (Mercado Aurora).

Tabelas: customers, products, orders, order_items. Determinístico por seed.
Receita = SUM(quantity * unit_price) de pedidos com status <> 'cancelado'.
"""
from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE customers (
    customer_id  INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    state        TEXT NOT NULL,          -- UF (SP, RJ, MG...)
    segment      TEXT NOT NULL,          -- 'varejo' | 'premium' | 'corporativo'
    signup_date  TEXT NOT NULL           -- AAAA-MM-DD
);
CREATE TABLE products (
    product_id   INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    category     TEXT NOT NULL,
    price        REAL NOT NULL           -- preço de tabela em R$
);
CREATE TABLE orders (
    order_id     INTEGER PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES customers(customer_id),
    order_date   TEXT NOT NULL,          -- AAAA-MM-DD
    status       TEXT NOT NULL,          -- 'entregue' | 'enviado' | 'cancelado'
    channel      TEXT NOT NULL           -- 'site' | 'app' | 'marketplace' | 'televendas'
);
CREATE TABLE order_items (
    order_id     INTEGER NOT NULL REFERENCES orders(order_id),
    product_id   INTEGER NOT NULL REFERENCES products(product_id),
    quantity     INTEGER NOT NULL,
    unit_price   REAL NOT NULL,          -- preço efetivo cobrado (pode ter desconto)
    PRIMARY KEY (order_id, product_id)
);
CREATE INDEX ix_orders_date ON orders(order_date);
CREATE INDEX ix_orders_customer ON orders(customer_id);
CREATE INDEX ix_items_product ON order_items(product_id);
"""

STATES = ["SP", "RJ", "MG", "PR", "RS", "SC", "BA", "PE", "CE", "GO", "DF", "ES"]
STATE_W = [30, 14, 11, 7, 6, 5, 6, 4, 4, 4, 3, 3]
CATEGORIES = {
    "Eletrônicos": (150, 4500), "Casa": (30, 900), "Moda": (40, 600), "Esporte": (50, 1200),
    "Beleza": (15, 400), "Livros": (20, 150), "Brinquedos": (25, 500), "Pet": (20, 350),
}
CHANNELS = ["site", "app", "marketplace", "televendas"]
CHANNEL_W = [40, 32, 22, 6]
SEGMENTS = ["varejo", "premium", "corporativo"]
SEGMENT_W = [72, 20, 8]
FIRST = ["Ana", "Bruno", "Carla", "Diego", "Elisa", "Fábio", "Gabriela", "Hugo", "Inês", "João",
         "Karen", "Lucas", "Marina", "Nuno", "Olívia", "Paulo", "Rita", "Sérgio", "Tânia", "Vitor"]
LAST = ["Silva", "Souza", "Costa", "Oliveira", "Pereira", "Lima", "Ferreira", "Almeida", "Ribeiro", "Rocha"]
ADJ = ["Pro", "Max", "Lite", "Plus", "Smart", "Eco", "Prime", "Classic"]
NOUN = {
    "Eletrônicos": ["Fone", "Smartwatch", "Tablet", "Caixa de Som", "Monitor"],
    "Casa": ["Panela", "Luminária", "Aspirador", "Cafeteira", "Ventilador"],
    "Moda": ["Jaqueta", "Tênis", "Camiseta", "Mochila", "Relógio"],
    "Esporte": ["Bicicleta", "Halter", "Esteira", "Bola", "Raquete"],
    "Beleza": ["Perfume", "Hidratante", "Secador", "Shampoo", "Kit Maquiagem"],
    "Livros": ["Romance", "Guia Prático", "Biografia", "Box Clássicos", "Ficção Científica"],
    "Brinquedos": ["Quebra-cabeça", "Boneco", "Carrinho", "Jogo de Tabuleiro", "Bloco de Montar"],
    "Pet": ["Ração", "Coleira", "Cama", "Brinquedo Pet", "Arranhador"],
}

START = date(2025, 1, 1)
END = date(2026, 10, 31)


def build_database(path: str | Path, seed: int = 2026, n_customers: int = 2000,
                   n_orders: int = 20000) -> Path:
    """Cria (ou recria) o banco sintético em ``path``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    rng = random.Random(seed)
    span = (END - START).days

    con = sqlite3.connect(path)
    con.executescript(SCHEMA_SQL)

    customers = []
    for cid in range(1, n_customers + 1):
        # crescimento: mais cadastros em datas recentes
        offset = int(span * rng.betavariate(1.6, 1.0))
        customers.append((cid, f"{rng.choice(FIRST)} {rng.choice(LAST)}",
                          rng.choices(STATES, STATE_W)[0], rng.choices(SEGMENTS, SEGMENT_W)[0],
                          (START + timedelta(days=offset)).isoformat()))
    con.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", customers)

    products, pid = [], 1
    for cat, (lo, hi) in CATEGORIES.items():
        for noun in NOUN[cat]:
            for adj in rng.sample(ADJ, 3):
                price = round(rng.uniform(lo, hi), 2)
                products.append((pid, f"{noun} {adj}", cat, price))
                pid += 1
    con.executemany("INSERT INTO products VALUES (?,?,?,?)", products)

    pop_w = [1 / (i + 1) ** 0.6 for i in range(len(products))]  # cauda longa de popularidade
    signup = {c[0]: date.fromisoformat(c[4]) for c in customers}
    seg = {c[0]: c[3] for c in customers}
    orders, items = [], []
    for oid in range(1, n_orders + 1):
        cid = rng.randint(1, n_customers)
        lo = signup[cid]
        if lo >= END:
            lo = END - timedelta(days=1)
        d = lo + timedelta(days=int((END - lo).days * rng.betavariate(1.3, 1.0)))
        r = rng.random()
        status = "cancelado" if r < 0.07 else "enviado" if (END - d).days < 6 and r < 0.6 else "entregue"
        orders.append((oid, cid, d.isoformat(), status, rng.choices(CHANNELS, CHANNEL_W)[0]))
        k = rng.choices([1, 2, 3, 4], [55, 28, 12, 5])[0]
        picked = set()
        for p in rng.choices(range(len(products)), pop_w, k=k):
            if p in picked:
                continue
            picked.add(p)
            prod = products[p]
            discount = rng.choice([0, 0, 0, 0.05, 0.10, 0.15])
            if seg[cid] == "premium":
                discount += 0.03
            items.append((oid, prod[0], rng.choices([1, 2, 3], [78, 17, 5])[0],
                          round(prod[3] * (1 - discount), 2)))
    con.executemany("INSERT INTO orders VALUES (?,?,?,?,?)", orders)
    con.executemany("INSERT INTO order_items VALUES (?,?,?,?)", items)
    con.commit()
    con.close()
    return path


def schema_description() -> str:
    """Esquema + convenções de negócio para o prompt do agente."""
    return (
        SCHEMA_SQL.strip()
        + "\n\nConvenções de negócio:\n"
        "- Receita = SUM(order_items.quantity * order_items.unit_price) considerando apenas orders.status <> 'cancelado'.\n"
        "- Ticket médio = receita / número de pedidos não cancelados.\n"
        "- Datas são texto ISO (AAAA-MM-DD): use strftime('%Y-%m', order_date) para agrupar por mês.\n"
        "- Dados vão de 2025-01-01 a 2026-10-31.\n"
        "- Dialeto: SQLite (sem ILIKE, sem DATE_TRUNC)."
    )
