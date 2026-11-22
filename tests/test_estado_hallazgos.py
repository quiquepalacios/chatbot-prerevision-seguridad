"""Hallazgo confirmado vs. riesgo a confirmar: validación, base, informe y prompt."""
from __future__ import annotations

import sqlite3

import pytest

from app.datos import db, informes
from app.seguridad.salida import validar_hallazgo
from app.servicios import prompts

BASE = {"titulo": "Ubicación del auto", "severidad": "alta", "fuentes": ["POL-01.1"],
        "requiere_seguridad": True, "nivel": "basico"}


@pytest.mark.parametrize("estado,esperado", [
    (None, "a_confirmar"),                    # sin estado: lo prudente
    ("confirmado", "confirmado"),
    ("Confirmado", "confirmado"),
    ("a confirmar", "a_confirmar"),
    ("por confirmar", "a_confirmar"),
    ("inventado", "a_confirmar"),
])
def test_estado_se_normaliza(estado, esperado):
    crudo = dict(BASE, **({"estado": estado} if estado is not None else {}))
    assert validar_hallazgo(crudo)["estado"] == esperado


def test_verificar_pasa_por_las_guardias():
    h = validar_hallazgo(dict(BASE, estado="a_confirmar",
                              verificar="¿El cliente 45678912 ve solo su auto? Puede pasar a producción."))
    assert "45678912" not in h["verificar"] and "[DNI REDACTADO]" in h["verificar"]
    assert "pasar a producción" not in h["verificar"]


def test_base_guarda_estado_y_migra_una_base_antigua(entorno):
    ruta = entorno["tmp"] / "antigua.db"
    con = sqlite3.connect(ruta)
    con.executescript("""
        CREATE TABLE sesiones (id TEXT PRIMARY KEY, usuario TEXT NOT NULL, creada TEXT NOT NULL);
        CREATE TABLE revisiones (id TEXT PRIMARY KEY, sesion_id TEXT NOT NULL, solucion TEXT NOT NULL,
            resumen TEXT, preguntas TEXT DEFAULT '[]', estado TEXT NOT NULL DEFAULT 'registrada',
            creada TEXT NOT NULL, actualizada TEXT NOT NULL);
        CREATE TABLE hallazgos (id INTEGER PRIMARY KEY AUTOINCREMENT, revision_id TEXT NOT NULL,
            titulo TEXT NOT NULL, severidad TEXT NOT NULL, descripcion TEXT, recomendacion TEXT,
            fuentes TEXT NOT NULL, requiere_seguridad INTEGER NOT NULL, nivel TEXT NOT NULL,
            fuente_recuperada INTEGER, creado TEXT NOT NULL);
    """)
    con.close()
    entorno["config"].RUTA_DB = ruta
    db.inicializar()                                           # agrega estado y verificar
    columnas = {f[1] for f in sqlite3.connect(ruta).execute("PRAGMA table_info(hallazgos)")}
    assert {"estado", "verificar"} <= columnas

    db.asegurar_sesion("s1", "lider")
    hallazgos = [validar_hallazgo(dict(BASE, estado="a_confirmar", verificar="¿Validan el dueño?")),
                 validar_hallazgo(dict(BASE, titulo="Login propio", estado="confirmado"))]
    rev = db.crear_revision("s1", "AutoConecta", "", [], hallazgos)
    guardados = {h["titulo"]: h for h in db.obtener_revision(rev, "s1")["hallazgos"]}
    assert guardados["Ubicación del auto"]["estado"] == "a_confirmar"
    assert guardados["Ubicación del auto"]["verificar"] == "¿Validan el dueño?"
    assert guardados["Login propio"]["estado"] == "confirmado"
    assert db.revisiones_de("s1")[0]["a_confirmar"] == 1
    assert db.metricas()["hallazgos_por_estado"] == {"a_confirmar": 1, "confirmado": 1}

    md = informes.generar(db.obtener_revision(rev, "s1"), "md")
    assert "**A confirmar** · severidad" in md and "**Qué confirmar:** ¿Validan el dueño?" in md
    assert "1 a confirmar con el equipo" in md
    csv = informes.generar(db.obtener_revision(rev, "s1"), "csv")
    assert csv.splitlines()[0].split(",")[3] == "estado" and ",a confirmar," in csv


def test_prompt_trata_las_solicitudes_a_alto_nivel():
    texto = prompts.sistema()
    assert "alto nivel" in texto and '"a_confirmar"' in texto and "verificar" in texto
    for pregunta in prompts.PREGUNTAS_OBLIGATORIAS:
        assert pregunta in texto
    assert "{" not in texto.split("Políticas de LSA-2026")[0]       # sin llaves sin reemplazar


def test_esquema_de_la_herramienta_pide_el_estado():
    from app.servicios.herramientas import ESQUEMAS

    registrar = next(e for e in ESQUEMAS if e["name"] == "registrar_revision")
    item = registrar["parameters"]["properties"]["hallazgos"]["items"]
    assert item["properties"]["estado"]["enum"] == ["confirmado", "a_confirmar"]
    assert "estado" in item["required"] and "verificar" in item["properties"]
