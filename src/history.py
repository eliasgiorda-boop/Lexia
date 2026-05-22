"""
Modulo de historial de consultas por usuario (SQLite).
La base vive en un solo archivo (history.db), sin servidor aparte.
Cada consulta se guarda asociada al username logueado.
"""
import sqlite3
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent.parent / "history.db"


def _conn():
    return sqlite3.connect(str(DB_PATH))


def init_db():
    """Crea la tabla si no existe. Idempotente."""
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS consultas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                query TEXT NOT NULL,
                fecha TEXT NOT NULL
            )
            """
        )


def guardar_consulta(username: str, query: str):
    """Guarda una consulta para un usuario."""
    with _conn() as c:
        c.execute(
            "INSERT INTO consultas (username, query, fecha) VALUES (?, ?, ?)",
            (username, query, datetime.now().strftime("%Y-%m-%d %H:%M")),
        )


def obtener_historial(username: str, limite: int = 20):
    """Devuelve las ultimas consultas de un usuario, mas recientes primero."""
    with _conn() as c:
        filas = c.execute(
            "SELECT query, fecha FROM consultas WHERE username = ? ORDER BY id DESC LIMIT ?",
            (username, limite),
        ).fetchall()
    return [{"query": q, "fecha": f} for q, f in filas]