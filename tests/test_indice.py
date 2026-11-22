"""Troceo, índice Chroma y MMR sin red: embedder falso y Chroma en carpeta temporal."""
from __future__ import annotations

import hashlib
import re
import shutil

import numpy as np
import pytest

from app import config
from app.rag import cargadores
from app.rag.fragmentos import Fragmento, fragmentar, sin_repetidos, trocear
from app.rag.indice import IndiceLineamientos, mmr

DIM = 256


def embedder_falso(textos):
    """Bolsa de palabras con hashing: parecido léxico = parecido vectorial."""
    matriz = np.zeros((len(textos), DIM), dtype="float32")
    for fila, texto in enumerate(textos):
        for palabra in re.findall(r"\w+", texto.lower()):
            matriz[fila, int(hashlib.md5(palabra.encode()).hexdigest(), 16) % DIM] += 1
    normas = np.linalg.norm(matriz, axis=1, keepdims=True)
    return matriz / np.clip(normas, 1e-9, None)


def _frag(ident, texto, tipo="owasp", cita=None):
    return Fragmento(ident, texto, {"cita": cita or ident, "tipo": tipo, "seccion": "",
                                    "fuente": cita or ident, "pagina": -1})


# ---------------------------------------------------------------------------
# Troceo
# ---------------------------------------------------------------------------
def test_trocear_respeta_tamano_y_solape():
    oraciones = [f"Esta es la oración número {i} con algunas palabras de relleno." for i in range(60)]
    chunks = trocear(" ".join(oraciones), max_palabras=50, overlap_palabras=10)
    assert len(chunks) > 5
    assert all(len(c.split()) <= 50 for c in chunks)
    # el final de un fragmento reaparece al inicio del siguiente
    assert chunks[0].split()[-5:] == chunks[1].split()[:10][-5:] or \
        set(chunks[0].split()[-10:]) & set(chunks[1].split()[:10])


def test_abreviaturas_no_cortan_oraciones():
    texto = "Use TLS, e.g. version 1.2 or later. See Sec. 3.2 for details."
    chunks = trocear(texto, max_palabras=6, overlap_palabras=0)
    assert "e.g. version 1.2 or later." in " ".join(chunks)
    assert not any(c.endswith("e.g.") for c in chunks)


def test_reglas_pol_pasan_enteras_con_metadatos():
    doc = cargadores.cargar(config.CARPETA_LINEAMIENTOS / "politicas_seguridad_banco_andesur.md")
    fragmentos = fragmentar(doc, "lineamientos/politicas.md", "ficticio")
    assert len(fragmentos) == 73
    f = fragmentos[0]
    assert f.id == "pol-01-1" and f.metadatos["cita"] == "POL-01.1"
    assert f.metadatos["requiere_revision"] is True and f.metadatos["pagina"] == -1
    # el embedding suma objetivo y referencias de la política; el texto visible no
    assert "API1:2023 (BOLA)" in f.texto_para_embedding() and "BOLA" not in f.texto
    assert all(isinstance(v, (str, int, float, bool)) for v in f.metadatos.values())


def test_sin_repetidos_quita_textos_iguales_y_desambigua_ids():
    fs = [_frag("a", "mismo texto"), _frag("b", "Mismo   TEXTO"), _frag("a", "otro texto")]
    unicos, quitados = sin_repetidos(fs)
    assert quitados == 1 and [f.id for f in unicos] == ["a", "a-1"]


# ---------------------------------------------------------------------------
# MMR
# ---------------------------------------------------------------------------
def test_mmr_prefiere_variedad():
    vectores = np.array([[1, 0], [0.99, 0.14], [0, 1]], dtype="float32")
    scores = [0.95, 0.94, 0.80]                 # el 2.º es casi copia del 1.º
    assert mmr(vectores, scores, [0, 1, 2], k=2, lam=0.5) == [0, 2]
    assert mmr(vectores, scores, [0, 1, 2], k=2, lam=1.0) == [0, 1]   # solo relevancia


# ---------------------------------------------------------------------------
# Índice Chroma
# ---------------------------------------------------------------------------
@pytest.fixture()
def indice(tmp_path):
    ix = IndiceLineamientos(carpeta=tmp_path / "chroma", coleccion="prueba",
                            embedder=embedder_falso)
    ix.construir([
        _frag("api1", "broken object level authorization validate owner of object id"),
        _frag("api1-copia", "broken object level authorization validate owner of object id again"),
        _frag("ssrf", "server side request forgery validate url allow list"),
        _frag("pol-01-1", "validar en el servidor que el recurso pertenece al usuario",
              tipo="lineamiento", cita="POL-01.1"),
        _frag("pol-08-1", "url externa lista blanca de dominios", tipo="lineamiento",
              cita="POL-08.1"),
    ], avisar=lambda _: None)
    yield ix
    ix._cliente = None
    shutil.rmtree(tmp_path / "chroma", ignore_errors=True)


def test_sin_indice_avisa_y_no_falla(tmp_path):
    ix = IndiceLineamientos(carpeta=tmp_path / "vacio", coleccion="nada",
                            embedder=embedder_falso)
    assert ix.buscar("lo que sea") == [] and not ix.disponible()
    assert "scripts.indexar" in ix.info()["aviso"]


def test_buscar_con_umbral_mmr_y_regla_interna(indice):
    assert indice.info()["fragmentos"] == 5
    pregunta = "broken object level authorization validate owner"
    r = indice.buscar(pregunta, k=3, umbral=0.0, lam=0.5)
    citas = [x.cita for x in r]
    assert citas[0] == "api1"
    assert "api1-copia" not in citas                 # MMR descarta la casi copia
    assert any(x.metadatos["tipo"] == "lineamiento" for x in r)

    # con umbral, la regla que no se parece a la pregunta no se fuerza
    r = indice.buscar(pregunta, k=2, umbral=0.1, lam=0.5)
    assert all(x.metadatos["tipo"] != "lineamiento" for x in r)


def test_garantia_reemplaza_el_ultimo_por_la_regla_interna(tmp_path):
    ix = IndiceLineamientos(carpeta=tmp_path / "chroma", coleccion="garantia", embedder=embedder_falso)
    ix.construir([_frag("a1", "alfa beta gamma delta"), _frag("a2", "alfa beta gamma epsilon"),
                  _frag("pol", "alfa zeta", tipo="lineamiento", cita="POL-99.1")],
                 avisar=lambda _: None)
    r = ix.buscar("alfa beta gamma delta epsilon", k=2, umbral=0.0, lam=1.0)
    assert [x.cita for x in r] == ["a1", "POL-99.1"]
    assert r[-1].metadatos["agregada"] == "regla interna garantizada"
    # con k=1 no se reemplaza el único fragmento relevante
    assert [x.cita for x in ix.buscar("alfa beta gamma delta", k=1, umbral=0.0)] == ["a1"]
    ix._cliente = None


def test_umbral_alto_no_devuelve_nada(indice):
    assert indice.buscar("receta de ceviche con limón", umbral=0.5) == []


def test_sin_garantia_y_con_filtro(indice):
    r = indice.buscar("server side request forgery url", k=1, umbral=0.1,
                      garantizar_interna=False)
    assert [x.cita for x in r] == ["ssrf"]
    r = indice.buscar("url externa", k=3, umbral=0.0, donde={"tipo": "lineamiento"})
    assert {x.metadatos["tipo"] for x in r} == {"lineamiento"}


def test_contexto_lleva_la_cita(indice):
    r = indice.buscar("validar recurso usuario servidor", k=1, umbral=0.0)
    assert indice.contexto(r).startswith("[POL-01.1]")


def test_reconstruir_reemplaza_sin_mezclar(indice):
    indice.construir([_frag("nuevo", "solo este fragmento queda en el índice")],
                     avisar=lambda _: None)
    assert indice.info()["fragmentos"] == 1
    nombres = {c.name for c in indice.cliente.list_collections()}
    assert nombres == {"prueba"}                      # la temporal ya no existe
