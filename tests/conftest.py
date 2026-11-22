"""conftest.py — entorno aislado: base de datos, logs, índice y modelo falsos.

Nada de red ni de clave: `RouterFalso` (patrón del curso) devuelve respuestas
preparadas y el índice usa Chroma en una carpeta temporal con un embedder de
bolsa de palabras. Así las pruebas miden el producto, no al proveedor.
"""
from __future__ import annotations

import json
from typing import Any, Callable

import pytest

from app.llm import RespuestaLLM, SinProveedores


class RouterFalso:
    """Devuelve las respuestas en orden y registra lo que recibió.

    Una respuesta puede ser una función `(mensajes) -> RespuestaLLM`: así el
    modelo falso puede usar el revision_id que le devolvió una herramienta.
    """

    def __init__(self, respuestas: list[Any] | None = None, fallar: bool = False) -> None:
        self.respuestas = list(respuestas or [])
        self.fallar = fallar
        self.llamadas: list[dict[str, Any]] = []

    def chat(self, mensajes, tools=None, **kwargs) -> RespuestaLLM:
        self.llamadas.append({"mensajes": [dict(m) for m in mensajes], "tools": tools, **kwargs})
        if self.fallar:
            raise SinProveedores("sin endpoint configurado (prueba)")
        if self.respuestas:
            siguiente = self.respuestas.pop(0)
            return siguiente(mensajes) if callable(siguiente) else siguiente
        return respuesta("respuesta de prueba")


def respuesta(texto: str = "", tool_calls: list[dict] | None = None) -> RespuestaLLM:
    return RespuestaLLM(texto=texto, tool_calls=tool_calls or [], modelo="falso", endpoint="test",
                        modo="test", uso={"prompt_tokens": 100, "completion_tokens": 20},
                        latencia=0.05)


def llamada(nombre: str, ident: str | None = None, **argumentos: Any) -> dict:
    return {"id": ident or f"call-{nombre}", "name": nombre, "arguments": argumentos}


def ultimo_resultado(mensajes, herramienta: str) -> dict[str, Any]:
    """El JSON que devolvió la última llamada a `herramienta`."""
    for m in reversed(mensajes):
        if m.get("role") == "tool" and m.get("name") == herramienta:
            return json.loads(m["content"])
    raise AssertionError(f"no hubo llamada a {herramienta}")


def despues_de(herramienta: str, armar: Callable[[dict[str, Any]], RespuestaLLM]):
    return lambda mensajes: armar(ultimo_resultado(mensajes, herramienta))


@pytest.fixture()
def entorno(tmp_path, monkeypatch):
    """Base de datos, logs e informes en un directorio temporal."""
    from app import config

    monkeypatch.setattr(config, "RUTA_DB", tmp_path / "revisiones.db")
    monkeypatch.setattr(config, "LOG_ARCHIVO", tmp_path / "logs" / "eventos.jsonl")
    monkeypatch.setattr(config, "CARPETA_INFORMES", tmp_path / "informes")
    monkeypatch.setattr(config, "LOG_CONSOLA", False)
    monkeypatch.setattr(config, "LOG_NIVEL", "INFO")
    from app.datos import db

    db.inicializar()
    return {"tmp": tmp_path, "config": config, "db": db}


FRAGMENTOS = [
    ("pol-01-1", "POL-01.1", "lineamiento",
     "POL-01.1 Todo endpoint que reciba un identificador de recurso debe validar en el servidor "
     "que el recurso pertenece al usuario autenticado bola autorizacion objeto"),
    ("pol-02-2", "POL-02.2", "lineamiento",
     "POL-02.2 Los codigos OTP deben tener al menos 6 digitos y bloquearse tras 5 intentos"),
    ("api1", "API1:2023", "owasp",
     "API1:2023 Broken Object Level Authorization bola validar dueño del objeto id endpoint"),
    ("api2", "API2:2023", "owasp",
     "API2:2023 Broken Authentication otp fuerza bruta limite de intentos"),
]


@pytest.fixture()
def indice(tmp_path):
    from app.rag.fragmentos import Fragmento
    from app.rag.indice import IndiceLineamientos
    from tests.test_indice import embedder_falso

    ix = IndiceLineamientos(carpeta=tmp_path / "chroma", coleccion="pruebas",
                            embedder=embedder_falso)
    ix.construir([Fragmento(i, t, {"cita": c, "fuente": c, "tipo": tipo, "seccion": "",
                                   "pagina": -1, "revision_obligatoria": "datos de cuentas"
                                   if tipo == "lineamiento" else ""})
                  for i, c, tipo, t in FRAGMENTOS], avisar=lambda _: None)
    yield ix
    ix._cliente = None


@pytest.fixture()
def servicio(entorno, indice, monkeypatch):
    """Servicio con índice pequeño y un modelo que responde lo que diga cada prueba."""
    from app import config
    from app.servicios.chat import ServicioChat

    monkeypatch.setattr(config, "RAG_UMBRAL", 0.05)          # escala del embedder falso
    router = RouterFalso()
    s = ServicioChat(router=router, indice=indice)
    s.router_falso = router
    return s
