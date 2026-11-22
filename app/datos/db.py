"""db.py — SQLite: memoria de la conversación, revisiones, hallazgos y métricas.

Regla heredada de la sesión 7, "un dato, un dueño": lo que se consulta o suma
(hallazgos, tokens, latencia) vive aquí; lo que explica qué pasó en un turno
vive en el log JSONL.

Tablas
    sesiones       una conversación (id que manda el front) y su usuario
    mensajes       lo que se dijo, YA ENMASCARADO (o una marca si se bloqueó)
    turnos         ruta, tokens, latencia, herramientas y citas recuperadas
    revisiones     una pre-revisión: solución, resumen, preguntas pendientes
    hallazgos      los hallazgos validados de cada revisión (con sus fuentes)
    escalamientos  lo que se deriva a Arquitectura de Seguridad

Cada revisión pertenece a una sesión: una herramienta no puede leer, exportar
ni escalar la revisión de otra sesión cambiando el id (el bot que revisa BOLA
no debería tener BOLA).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator

from app import config

_CANDADO = threading.Lock()

ESQUEMA = """
CREATE TABLE IF NOT EXISTS sesiones (
    id          TEXT PRIMARY KEY,
    usuario     TEXT NOT NULL,
    creada      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mensajes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sesion_id   TEXT NOT NULL REFERENCES sesiones(id) ON DELETE CASCADE,
    turno_id    TEXT NOT NULL,
    rol         TEXT NOT NULL CHECK (rol IN ('user', 'assistant')),
    contenido   TEXT NOT NULL,
    creado      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_mensajes_sesion ON mensajes(sesion_id, id);
CREATE TABLE IF NOT EXISTS turnos (
    turno_id     TEXT PRIMARY KEY,
    sesion_id    TEXT NOT NULL REFERENCES sesiones(id) ON DELETE CASCADE,
    ruta         TEXT NOT NULL,
    fuente       TEXT,
    motivo       TEXT,
    faq_id       TEXT,
    similitud    REAL,
    modelo       TEXT,
    tokens_entrada INTEGER DEFAULT 0,
    tokens_salida  INTEGER DEFAULT 0,
    latencia_ms  INTEGER DEFAULT 0,
    herramientas TEXT DEFAULT '[]',
    citas        TEXT DEFAULT '[]',
    enmascarados TEXT DEFAULT '{}',
    creado       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_turnos_sesion ON turnos(sesion_id);
CREATE TABLE IF NOT EXISTS revisiones (
    id          TEXT PRIMARY KEY,
    sesion_id   TEXT NOT NULL REFERENCES sesiones(id) ON DELETE CASCADE,
    solucion    TEXT NOT NULL,
    resumen     TEXT,
    preguntas   TEXT DEFAULT '[]',
    estado      TEXT NOT NULL DEFAULT 'registrada',
    creada      TEXT NOT NULL,
    actualizada TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hallazgos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    revision_id   TEXT NOT NULL REFERENCES revisiones(id) ON DELETE CASCADE,
    titulo        TEXT NOT NULL,
    severidad     TEXT NOT NULL CHECK (severidad IN ('alta', 'media', 'baja')),
    descripcion   TEXT,
    recomendacion TEXT,
    fuentes       TEXT NOT NULL,
    requiere_seguridad INTEGER NOT NULL,
    nivel         TEXT NOT NULL,
    estado        TEXT NOT NULL DEFAULT 'a_confirmar'
                  CHECK (estado IN ('confirmado', 'a_confirmar')),
    verificar     TEXT,
    fuente_recuperada INTEGER,
    creado        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS escalamientos (
    id          TEXT PRIMARY KEY,
    revision_id TEXT NOT NULL REFERENCES revisiones(id) ON DELETE CASCADE,
    motivo      TEXT NOT NULL,
    puntos      TEXT DEFAULT '[]',
    creado      TEXT NOT NULL
);
"""


def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ruta() -> Path:
    return Path(config.RUTA_DB)


@contextmanager
def conexion() -> Iterator[sqlite3.Connection]:
    ruta = _ruta()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with _CANDADO:
        con = sqlite3.connect(ruta, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()


def inicializar() -> None:
    with conexion() as con:
        con.execute("PRAGMA journal_mode = WAL")
        con.executescript(ESQUEMA)
        _migrar(con)
    purgar_antiguos()


# Columnas agregadas después de la primera versión: una base existente las recibe
# al arrancar (SQLite no las agrega con CREATE TABLE IF NOT EXISTS).
MIGRACIONES = {
    "hallazgos": {
        "estado": "TEXT NOT NULL DEFAULT 'a_confirmar'",
        "verificar": "TEXT",
    },
}


def _migrar(con: sqlite3.Connection) -> None:
    for tabla, columnas in MIGRACIONES.items():
        existentes = {f["name"] for f in con.execute(f"PRAGMA table_info({tabla})")}
        for nombre, definicion in columnas.items():
            if nombre not in existentes:
                con.execute(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {definicion}")


def purgar_antiguos(dias: int | None = None) -> int:
    """Retención: las sesiones más viejas que DB_RETENCION_DIAS se borran enteras."""
    limite = (datetime.now() - timedelta(days=dias or config.RETENCION_DIAS)).isoformat()
    with conexion() as con:
        return con.execute("DELETE FROM sesiones WHERE creada < ?", (limite,)).rowcount


# ---------------------------------------------------------------------------
# Sesiones y mensajes
# ---------------------------------------------------------------------------
def asegurar_sesion(sesion_id: str, usuario: str) -> None:
    with conexion() as con:
        con.execute("INSERT OR IGNORE INTO sesiones (id, usuario, creada) VALUES (?, ?, ?)",
                    (sesion_id, usuario, _ahora()))


def guardar_mensaje(sesion_id: str, turno_id: str, rol: str, contenido: str) -> None:
    with conexion() as con:
        con.execute("INSERT INTO mensajes (sesion_id, turno_id, rol, contenido, creado) "
                    "VALUES (?, ?, ?, ?, ?)", (sesion_id, turno_id, rol, contenido, _ahora()))


def mensajes_de(sesion_id: str, limite: int = 0) -> list[dict[str, Any]]:
    """Los últimos `limite` mensajes en orden cronológico (0 = todos)."""
    with conexion() as con:
        filas = con.execute(
            "SELECT rol, contenido, turno_id, creado FROM mensajes WHERE sesion_id = ? "
            "ORDER BY id DESC" + (" LIMIT ?" if limite else ""),
            (sesion_id, limite) if limite else (sesion_id,)).fetchall()
    return [dict(f) for f in reversed(filas)]


def borrar_sesion(sesion_id: str) -> int:
    with conexion() as con:
        return con.execute("DELETE FROM sesiones WHERE id = ?", (sesion_id,)).rowcount


# ---------------------------------------------------------------------------
# Turnos
# ---------------------------------------------------------------------------
def guardar_turno(turno_id: str, sesion_id: str, ruta: str, *, fuente: str = "",
                  motivo: str = "", faq_id: str | None = None, similitud: float | None = None,
                  modelo: str = "", tokens: tuple[int, int] = (0, 0), latencia_ms: int = 0,
                  herramientas: list[dict] | None = None, citas: list[str] | None = None,
                  enmascarados: dict[str, int] | None = None) -> None:
    with conexion() as con:
        con.execute(
            "INSERT OR REPLACE INTO turnos (turno_id, sesion_id, ruta, fuente, motivo, faq_id, "
            "similitud, modelo, tokens_entrada, tokens_salida, latencia_ms, herramientas, citas, "
            "enmascarados, creado) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (turno_id, sesion_id, ruta, fuente, motivo, faq_id, similitud, modelo,
             int(tokens[0]), int(tokens[1]), int(latencia_ms),
             json.dumps(herramientas or [], ensure_ascii=False, default=str),
             json.dumps(citas or [], ensure_ascii=False),
             json.dumps(enmascarados or {}, ensure_ascii=False), _ahora()))


def citas_de_sesion(sesion_id: str) -> set[str]:
    """Citas que buscar_lineamiento devolvió en esta conversación."""
    with conexion() as con:
        filas = con.execute("SELECT citas FROM turnos WHERE sesion_id = ?", (sesion_id,)).fetchall()
    citas: set[str] = set()
    for f in filas:
        citas.update(json.loads(f["citas"] or "[]"))
    return citas


# ---------------------------------------------------------------------------
# Revisiones, hallazgos y escalamientos
# ---------------------------------------------------------------------------
def _id(prefijo: str) -> str:
    return f"{prefijo}-{uuid.uuid4().hex[:6].upper()}"


def crear_revision(sesion_id: str, solucion: str, resumen: str, preguntas: list[str],
                   hallazgos: list[dict[str, Any]]) -> str:
    revision_id = _id("REV")
    ahora = _ahora()
    with conexion() as con:
        con.execute("INSERT INTO revisiones (id, sesion_id, solucion, resumen, preguntas, creada, "
                    "actualizada) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (revision_id, sesion_id, solucion, resumen,
                     json.dumps(preguntas, ensure_ascii=False), ahora, ahora))
        _insertar_hallazgos(con, revision_id, hallazgos)
    return revision_id


def actualizar_revision(revision_id: str, sesion_id: str, resumen: str | None,
                        preguntas: list[str] | None, hallazgos: list[dict[str, Any]]) -> bool:
    """Reemplaza los hallazgos de una revisión existente (la conversación avanzó)."""
    with conexion() as con:
        fila = con.execute("SELECT id FROM revisiones WHERE id = ? AND sesion_id = ?",
                           (revision_id, sesion_id)).fetchone()
        if not fila:
            return False
        con.execute("DELETE FROM hallazgos WHERE revision_id = ?", (revision_id,))
        _insertar_hallazgos(con, revision_id, hallazgos)
        con.execute("UPDATE revisiones SET resumen = COALESCE(?, resumen), "
                    "preguntas = COALESCE(?, preguntas), actualizada = ? WHERE id = ?",
                    (resumen, json.dumps(preguntas, ensure_ascii=False) if preguntas is not None
                     else None, _ahora(), revision_id))
    return True


def _insertar_hallazgos(con: sqlite3.Connection, revision_id: str,
                        hallazgos: list[dict[str, Any]]) -> None:
    ahora = _ahora()
    con.executemany(
        "INSERT INTO hallazgos (revision_id, titulo, severidad, descripcion, recomendacion, "
        "fuentes, requiere_seguridad, nivel, estado, verificar, fuente_recuperada, creado) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(revision_id, h["titulo"], h["severidad"], h.get("descripcion", ""),
          h.get("recomendacion", ""), json.dumps(h["fuentes"], ensure_ascii=False),
          int(bool(h["requiere_seguridad"])), h.get("nivel", "basico"),
          h.get("estado", "a_confirmar"), h.get("verificar", ""),
          None if h.get("fuente_recuperada") is None else int(bool(h["fuente_recuperada"])),
          ahora) for h in hallazgos])


def obtener_revision(revision_id: str, sesion_id: str | None = None) -> dict[str, Any] | None:
    """La revisión con sus hallazgos y escalamientos. Con `sesion_id`, solo si es suya."""
    with conexion() as con:
        consulta = "SELECT * FROM revisiones WHERE id = ?"
        params: tuple = (revision_id,)
        if sesion_id is not None:
            consulta += " AND sesion_id = ?"
            params = (revision_id, sesion_id)
        fila = con.execute(consulta, params).fetchone()
        if not fila:
            return None
        hallazgos = con.execute("SELECT * FROM hallazgos WHERE revision_id = ? ORDER BY "
                                "CASE severidad WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END, "
                                "requiere_seguridad DESC, id", (revision_id,)).fetchall()
        escalamientos = con.execute("SELECT * FROM escalamientos WHERE revision_id = ? ORDER BY "
                                    "creado", (revision_id,)).fetchall()
    revision = dict(fila)
    revision["preguntas"] = json.loads(revision["preguntas"] or "[]")
    revision["hallazgos"] = [
        {**dict(h), "fuentes": json.loads(h["fuentes"]),
         "requiere_seguridad": bool(h["requiere_seguridad"]),
         "fuente_recuperada": None if h["fuente_recuperada"] is None
         else bool(h["fuente_recuperada"])}
        for h in hallazgos]
    revision["escalamientos"] = [{**dict(e), "puntos": json.loads(e["puntos"] or "[]")}
                                 for e in escalamientos]
    return revision


def revisiones_de(sesion_id: str) -> list[dict[str, Any]]:
    with conexion() as con:
        filas = con.execute(
            "SELECT r.id, r.solucion, r.estado, r.actualizada, COUNT(h.id) AS hallazgos, "
            "SUM(h.requiere_seguridad) AS a_seguridad, "
            "SUM(h.estado = 'a_confirmar') AS a_confirmar FROM revisiones r "
            "LEFT JOIN hallazgos h ON h.revision_id = r.id WHERE r.sesion_id = ? "
            "GROUP BY r.id ORDER BY r.actualizada DESC", (sesion_id,)).fetchall()
    return [dict(f) for f in filas]


def escalar(revision_id: str, sesion_id: str, motivo: str, puntos: list[str]) -> str | None:
    with conexion() as con:
        fila = con.execute("SELECT id FROM revisiones WHERE id = ? AND sesion_id = ?",
                           (revision_id, sesion_id)).fetchone()
        if not fila:
            return None
        escalamiento_id = _id("ESC")
        con.execute("INSERT INTO escalamientos (id, revision_id, motivo, puntos, creado) "
                    "VALUES (?, ?, ?, ?, ?)", (escalamiento_id, revision_id, motivo,
                                                json.dumps(puntos, ensure_ascii=False), _ahora()))
        con.execute("UPDATE revisiones SET estado = 'escalada', actualizada = ? WHERE id = ?",
                    (_ahora(), revision_id))
    return escalamiento_id


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------
def metricas() -> dict[str, Any]:
    with conexion() as con:
        rutas = {f["ruta"]: f["n"] for f in con.execute(
            "SELECT ruta, COUNT(*) AS n FROM turnos GROUP BY ruta")}
        tot = con.execute(
            "SELECT COUNT(*) AS turnos, COALESCE(SUM(tokens_entrada), 0) AS entrada, "
            "COALESCE(SUM(tokens_salida), 0) AS salida, COALESCE(AVG(latencia_ms), 0) AS ms "
            "FROM turnos").fetchone()
        latencias = [f["latencia_ms"] for f in con.execute(
            "SELECT latencia_ms FROM turnos WHERE ruta = 'modelo' ORDER BY latencia_ms")]
        severidades = {f["severidad"]: f["n"] for f in con.execute(
            "SELECT severidad, COUNT(*) AS n FROM hallazgos GROUP BY severidad")}
        estados = {f["estado"]: f["n"] for f in con.execute(
            "SELECT estado, COUNT(*) AS n FROM hallazgos GROUP BY estado")}
        revisiones = con.execute("SELECT COUNT(*) AS n, SUM(estado = 'escalada') AS escaladas "
                                 "FROM revisiones").fetchone()

    def percentil(p: float) -> int:
        if not latencias:
            return 0
        return int(latencias[min(len(latencias) - 1, int(round(p * (len(latencias) - 1))))])

    return {
        "turnos": tot["turnos"], "por_ruta": rutas,
        "tokens": {"entrada": tot["entrada"], "salida": tot["salida"]},
        "latencia_ms": {"promedio": int(tot["ms"]), "p50_modelo": percentil(0.5),
                        "p95_modelo": percentil(0.95)},
        "revisiones": revisiones["n"], "escaladas": revisiones["escaladas"] or 0,
        "hallazgos_por_severidad": severidades,
        "hallazgos_por_estado": estados,
    }


__all__ = ["actualizar_revision", "asegurar_sesion", "borrar_sesion", "citas_de_sesion",
           "conexion", "crear_revision", "escalar", "guardar_mensaje", "guardar_turno",
           "inicializar", "mensajes_de", "metricas", "obtener_revision", "purgar_antiguos",
           "revisiones_de"]
