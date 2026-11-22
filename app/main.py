"""main.py — API FastAPI del chatbot de pre-revisión (adaptada de la sesión 7).

    GET    /                                      chat web
    GET    /api/health                            estado: modelo, índice, FAQ, configuración
    POST   /api/chat                              un turno completo
    GET    /api/sesiones/{sesion}/mensajes        historial guardado (ya enmascarado)
    DELETE /api/sesiones/{sesion}                 borra la conversación y sus revisiones
    GET    /api/sesiones/{sesion}/revisiones      revisiones de la conversación
    GET    /api/revisiones/{id}?sesion_id=        una revisión con sus hallazgos
    GET    /api/revisiones/{id}/informe.{md|csv}?sesion_id=   informe descargable
    GET    /api/buscar?q=                         prueba de recuperación (top-k -> MMR)
    GET    /api/ejemplos                          casos de prueba para la demo
    GET    /api/logs · /api/metricas              log del turno y métricas

Arranque: ejecuta `run.bat --iniciar` o
    python -m uvicorn app.main:app --port 8040

Es un servicio local de demostración: no tiene usuarios ni contraseñas. Las
revisiones se aíslan por sesión (una sesión no puede leer las de otra).
"""
from __future__ import annotations

import re
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Path as PathParam, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import config
from app.datos import db, informes, logs
from app.servicios.chat import ErrorEntrada, ServicioChat

ARRANQUE = time.time()
ESTATICOS = config.APP_DIR / "static"
PATRON_SESION = r"^[A-Za-z0-9_-]{1,64}$"
PATRON_REVISION = r"^REV-[0-9A-F]{6}$"

_SERVICIO: ServicioChat | None = None
_ERROR_ARRANQUE: str | None = None
_CANDADO = threading.Lock()


def obtener_servicio() -> ServicioChat:
    """Se construye una sola vez, con candado (dos peticiones al arrancar no cargan
    dos veces el modelo de embeddings)."""
    global _SERVICIO
    if _SERVICIO is None:
        with _CANDADO:
            if _SERVICIO is None:
                _SERVICIO = ServicioChat()
    return _SERVICIO


def _precalentar() -> None:
    """Carga el servicio y el modelo de embeddings en segundo plano: el servidor
    abre al instante y la primera pregunta no espera ~30 s."""
    global _ERROR_ARRANQUE
    try:
        servicio = obtener_servicio()
        info = servicio.indice.info()
        if info.get("indexado"):
            servicio.indice.buscar("precalentar el modelo de embeddings", k=1)
        logs.info("servicio.listo", fragmentos=info.get("fragmentos", 0),
                  indexado=info.get("indexado", False))
    except Exception as exc:                          # noqa: BLE001
        _ERROR_ARRANQUE = f"{type(exc).__name__}: {exc}"
        logs.error("servicio.error", motivo=_ERROR_ARRANQUE)


@asynccontextmanager
async def ciclo_de_vida(_: FastAPI):
    threading.Thread(target=_precalentar, daemon=True, name="precalentar").start()
    yield


app = FastAPI(
    title="Pre-revisión de seguridad — Banco Andesur (caso ficticio)",
    description=("Chatbot RAG que hace una primera revisión de seguridad de arquitecturas web y "
                 "APIs. " + config.AVISO_SEGURIDAD),
    version="1.0.0",
    lifespan=ciclo_de_vida,
)
if ESTATICOS.is_dir():
    app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")


@app.middleware("http")
async def cabeceras_de_seguridad(request: Request, call_next):
    """Lo mínimo que el propio bot recomendaría (ASVS V3.4): sin sniffing, sin iframes,
    CSP restrictiva y sin caché para las respuestas de la API."""
    respuesta = await call_next(request)
    respuesta.headers.setdefault("X-Content-Type-Options", "nosniff")
    respuesta.headers.setdefault("X-Frame-Options", "DENY")
    respuesta.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path.startswith(("/docs", "/redoc", "/openapi")):
        return respuesta                               # Swagger usa su propio CDN
    respuesta.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
    if request.url.path.startswith("/api/"):
        respuesta.headers.setdefault("Cache-Control", "no-store")
    return respuesta


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------
class PeticionChat(BaseModel):
    mensaje: str = Field(..., max_length=config.LIMITE_MENSAJE + 1000,
                         description="Descripción de la arquitectura o pregunta")
    usuario_id: str = Field(default="lider-tecnico", pattern=PATRON_SESION)
    sesion_id: str = Field(default="demo", pattern=PATRON_SESION)


class RespuestaChatModelo(BaseModel):
    respuesta: str
    sesion_id: str
    usuario_id: str
    turno_id: str
    ruta: str
    motivo: str = ""
    fuente: str = ""
    faq_id: str | None = None
    similitud: float | None = None
    revision_id: str | None = None
    hallazgos: list[dict[str, Any]] = []
    rechazados: list[dict[str, Any]] = []
    escalamientos: list[str] = []
    informes: list[dict[str, str]] = []
    documentos: list[dict[str, Any]] = []
    herramientas: list[dict[str, Any]] = []
    enmascarados: dict[str, int] = {}
    modo: str = "real"
    modelo: str = "—"
    latencia_ms: int = 0
    tokens: list[int] = []
    aviso: str | None = None


def _servicio_listo() -> ServicioChat:
    if _ERROR_ARRANQUE:
        raise HTTPException(503, f"El servicio no pudo arrancar: {_ERROR_ARRANQUE}")
    return obtener_servicio()


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
def portada() -> Any:
    index = ESTATICOS / "index.html"
    if index.is_file():
        return FileResponse(index)
    return {"mensaje": "API de pre-revisión de seguridad", "docs": "/docs"}


@app.get("/api/health")
def health() -> dict[str, Any]:
    servicio = _SERVICIO
    base = {"uptime_s": int(time.time() - ARRANQUE), "aviso": config.AVISO_SEGURIDAD,
            "config": config.resumen()}
    if servicio is None:
        return {**base, "estado": "error" if _ERROR_ARRANQUE else "iniciando",
                "error": _ERROR_ARRANQUE, "modelo": "cargando…", "modo": "—", "indice": {}}
    endpoints = getattr(servicio.router, "endpoints", []) or []
    return {
        **base, "estado": "ok",
        "modelo": getattr(endpoints[0], "modelo", "") or "sin modelo" if endpoints else "sin modelo",
        "modo": "real" if endpoints else "demo",
        "indice": servicio.indice.info(),
        "faq": servicio.faq.info(),
    }


@app.post("/api/chat", response_model=RespuestaChatModelo)
def chat(peticion: PeticionChat) -> RespuestaChatModelo:
    try:
        resultado = _servicio_listo().responder(peticion.mensaje, peticion.usuario_id,
                                                peticion.sesion_id)
    except ErrorEntrada as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RespuestaChatModelo(**resultado.to_dict())


Sesion = PathParam(..., pattern=PATRON_SESION)
Revision = PathParam(..., pattern=PATRON_REVISION)


@app.get("/api/sesiones/{sesion_id}/mensajes")
def mensajes(sesion_id: str = Sesion, limite: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    items = db.mensajes_de(sesion_id, limite)
    return {"sesion_id": sesion_id, "total": len(items), "mensajes": items}


@app.delete("/api/sesiones/{sesion_id}")
def borrar_sesion(sesion_id: str = Sesion) -> dict[str, Any]:
    borradas = db.borrar_sesion(sesion_id)
    logs.info("sesion.borrada", sesion=sesion_id, borradas=borradas)
    return {"sesion_id": sesion_id, "borrada": bool(borradas)}


@app.get("/api/sesiones/{sesion_id}/revisiones")
def revisiones(sesion_id: str = Sesion) -> dict[str, Any]:
    return {"sesion_id": sesion_id, "revisiones": db.revisiones_de(sesion_id)}


def _revision_de(revision_id: str, sesion_id: str) -> dict[str, Any]:
    revision = db.obtener_revision(revision_id, sesion_id)
    if revision is None:                       # misma respuesta si no existe o no es suya
        raise HTTPException(404, "Revisión no encontrada en esta sesión.")
    return revision


@app.get("/api/revisiones/{revision_id}")
def revision(revision_id: str = Revision,
             sesion_id: str = Query(..., pattern=PATRON_SESION)) -> dict[str, Any]:
    return _revision_de(revision_id, sesion_id)


@app.get("/api/revisiones/{revision_id}/informe.{formato}")
def informe(revision_id: str = Revision, formato: str = PathParam(..., pattern=r"^(md|csv)$"),
            sesion_id: str = Query(..., pattern=PATRON_SESION)) -> Response:
    revision_data = _revision_de(revision_id, sesion_id)
    contenido = informes.generar(revision_data, formato)
    tipo = "text/markdown; charset=utf-8" if formato == "md" else "text/csv; charset=utf-8"
    if formato == "csv":
        contenido = "﻿" + contenido             # Excel abre bien las tildes
    nombre = informes.nombre_archivo(revision_id, formato)
    return Response(contenido, media_type=tipo,
                    headers={"Content-Disposition": f'attachment; filename="{nombre}"'})


@app.get("/api/buscar")
def buscar(q: str = Query(..., min_length=2, max_length=500)) -> dict[str, Any]:
    """Para ver qué recupera el RAG sin pasar por el modelo (útil en la demo)."""
    from app.seguridad.entrada import enmascarar

    servicio = _servicio_listo()
    consulta, _ = enmascarar(q)
    resultados = servicio.indice.buscar(consulta)
    return {"consulta": consulta, "umbral": config.RAG_UMBRAL,
            "k": [config.RAG_K_INICIAL, config.RAG_K_FINAL],
            "resultados": [r.to_dict() for r in resultados], "aviso": servicio.indice.aviso or None}


RE_EJEMPLO = re.compile(r"^(caso|trampa)_[a-z0-9_]+\.txt$")


@app.get("/api/ejemplos")
def ejemplos() -> dict[str, Any]:
    """Casos de prueba de casos_prueba/ (nunca la hoja de respuestas)."""
    carpeta = Path(config.CARPETA_CASOS)
    items = []
    if carpeta.is_dir():
        for ruta in sorted(carpeta.iterdir()):
            if ruta.is_file() and RE_EJEMPLO.match(ruta.name):
                texto = ruta.read_text("utf-8")
                items.append({"id": ruta.stem, "titulo": ruta.stem.replace("_", " "),
                              "texto": texto[:config.LIMITE_MENSAJE]})
    return {"ejemplos": items}


@app.get("/api/logs")
def ver_logs(n: int = Query(80, ge=1, le=500), nivel: str | None = Query(None, pattern=r"^[A-Z]+$"),
             turno: str | None = Query(None, pattern=r"^t-[0-9a-f]{6}$")) -> dict[str, Any]:
    eventos = logs.leer(limite=n, nivel=nivel, turno=turno)
    return {"total": len(eventos), "eventos": eventos}


@app.get("/api/metricas")
def metricas() -> dict[str, Any]:
    return db.metricas()


@app.exception_handler(Exception)
async def error_no_controlado(request: Request, exc: Exception) -> JSONResponse:
    """Nunca devolver la traza al cliente (ASVS V16.5); el detalle va al log."""
    logs.error("api.error", ruta=request.url.path, tipo=type(exc).__name__, detalle=str(exc)[:300])
    return JSONResponse(status_code=500, content={"detail": "Error interno. Revisa el log del servidor."})


if __name__ == "__main__":
    import uvicorn

    print(f"Pre-revisión de seguridad en http://127.0.0.1:{config.PUERTO}")
    uvicorn.run("app.main:app", host="127.0.0.1", port=config.PUERTO, reload=False)
