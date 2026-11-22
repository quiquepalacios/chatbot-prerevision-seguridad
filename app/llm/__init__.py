"""Acceso al modelo de lenguaje.

`llm_router.py` es una copia del router del curso (`_comun/llm_router.py`, DMC),
incluida aquí para que el repositorio funcione solo, sin la carpeta del curso.
Si se actualiza el router del curso, se vuelve a copiar este archivo.
"""
from app.llm.llm_router import (
    Endpoint,
    RespuestaLLM,
    Router,
    SinProveedores,
    crear_router,
    listar_endpoints,
)

__all__ = ["Endpoint", "RespuestaLLM", "Router", "SinProveedores", "crear_router",
           "listar_endpoints"]
