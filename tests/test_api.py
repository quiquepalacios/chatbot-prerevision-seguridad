"""La API FastAPI con TestClient: mismo servicio de pruebas (sin red ni clave)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import config, main
from tests.conftest import despues_de, llamada, respuesta
from tests.test_turno import HALLAZGO_BOLA


@pytest.fixture()
def cliente(servicio, monkeypatch):
    monkeypatch.setattr(main, "_SERVICIO", servicio)
    monkeypatch.setattr(main, "_ERROR_ARRANQUE", None)
    with TestClient(main.app) as c:
        c.servicio = servicio
        yield c


def _chat(cliente, mensaje, sesion="s-api"):
    return cliente.post("/api/chat", json={"mensaje": mensaje, "sesion_id": sesion})


def test_portada_y_cabeceras_de_seguridad(cliente):
    r = cliente.get("/")
    assert r.status_code == 200 and "Pre-revisión de seguridad" in r.text
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert cliente.get("/api/health").headers["Cache-Control"] == "no-store"


def test_health(cliente):
    d = cliente.get("/api/health").json()
    assert d["estado"] == "ok" and d["modo"] == "demo" and d["indice"]["indexado"]
    assert d["aviso"] == config.AVISO_SEGURIDAD and d["faq"]["faqs"] >= 10


def test_chat_faq_y_error_422(cliente):
    d = _chat(cliente, "¿Qué es BOLA?").json()
    assert d["ruta"] == "faq" and d["faq_id"] == "FAQ-BOLA"
    r = _chat(cliente, "   ")
    assert r.status_code == 422 and "vacía" in r.json()["detail"]


@pytest.mark.parametrize("sesion", ["../etc", "a" * 65, "con espacio", "s;drop"])
def test_sesion_invalida(cliente, sesion):
    assert _chat(cliente, "hola", sesion).status_code == 422


def test_revision_informes_y_aislamiento_por_sesion(cliente):
    cliente.servicio.router_falso.respuestas = [
        respuesta(tool_calls=[llamada("buscar_lineamiento", consulta="BOLA validar dueño")]),
        respuesta(tool_calls=[llamada("registrar_revision", solucion="AutoConecta",
                                      hallazgos=[HALLAZGO_BOLA])]),
        despues_de("registrar_revision", lambda r: respuesta(f"Registré {r['revision_id']}.")),
    ]
    d = _chat(cliente, "API de vehículos: GET /vehiculos/{id}/ubicacion solo pide token").json()
    rev = d["revision_id"]
    assert rev and d["hallazgos"][0]["fuentes"] == ["POL-01.1", "API1:2023"]

    r = cliente.get(f"/api/revisiones/{rev}", params={"sesion_id": "s-api"})
    assert r.status_code == 200 and r.json()["hallazgos"][0]["requiere_seguridad"] is True
    md = cliente.get(f"/api/revisiones/{rev}/informe.md", params={"sesion_id": "s-api"})
    assert md.status_code == 200 and "va a Seguridad" in md.text
    assert md.headers["content-disposition"] == f'attachment; filename="{rev}.md"'
    csv = cliente.get(f"/api/revisiones/{rev}/informe.csv", params={"sesion_id": "s-api"})
    assert csv.text.startswith("﻿revision_id,") and "POL-01.1; API1:2023" in csv.text

    # otra sesión no la ve (404, igual que si no existiera)
    assert cliente.get(f"/api/revisiones/{rev}", params={"sesion_id": "otra"}).status_code == 404
    assert cliente.get(f"/api/revisiones/{rev}/informe.md",
                       params={"sesion_id": "otra"}).status_code == 404
    assert cliente.get(f"/api/revisiones/{rev}/informe.exe",
                       params={"sesion_id": "s-api"}).status_code in (404, 422)
    assert cliente.get("/api/revisiones/REV-../informe.md",
                       params={"sesion_id": "s-api"}).status_code in (404, 422)

    lista = cliente.get("/api/sesiones/s-api/revisiones").json()["revisiones"]
    assert lista[0]["id"] == rev and lista[0]["a_seguridad"] == 1


def test_historial_borrado_logs_y_metricas(cliente):
    _chat(cliente, "Mi DNI es 45678912, ¿qué es BOLA?")
    mensajes = cliente.get("/api/sesiones/s-api/mensajes").json()["mensajes"]
    assert mensajes and "45678912" not in str(mensajes)
    assert cliente.get("/api/logs", params={"n": 20}).json()["total"] > 0
    assert cliente.get("/api/metricas").json()["turnos"] == 1
    assert cliente.delete("/api/sesiones/s-api").json()["borrada"] is True
    assert cliente.get("/api/sesiones/s-api/mensajes").json()["total"] == 0


def test_buscar_y_ejemplos(cliente):
    d = cliente.get("/api/buscar", params={"q": "bola autorizacion objeto"}).json()
    assert d["resultados"] and d["k"] == [config.RAG_K_INICIAL, config.RAG_K_FINAL]
    ejemplos = cliente.get("/api/ejemplos").json()["ejemplos"]
    ids = [e["id"] for e in ejemplos]
    assert "caso_01_autoconecta" in ids and not any("respuestas" in i for i in ids)


def test_error_interno_no_expone_la_traza(cliente, monkeypatch):
    def explota(*_a, **_k):
        raise RuntimeError("detalle interno secreto")

    monkeypatch.setattr(cliente.servicio, "responder", explota)
    with TestClient(main.app, raise_server_exceptions=False) as c:
        r = c.post("/api/chat", json={"mensaje": "hola", "sesion_id": "s-x"})
    assert r.status_code == 500 and "secreto" not in r.text
