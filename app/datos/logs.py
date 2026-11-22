"""logs.py — el registro del turno: consola en una línea y archivo JSONL.

Adaptado del producto de la sesión 7 del curso.

Regla de la sesión 7: **un dato, un dueño**.

- Si el dato se consulta, filtra o suma (revisiones, hallazgos, tokens,
  latencia) su dueño es la base de datos. El log solo repite el número en la
  línea de cierre del turno.
- Si el dato sirve para explicar qué pasó (un filtro que saltó, una caída a
  demo, una herramienta que tardó) su dueño es el log.
- El valor original de un DNI, una tarjeta, una IP o una clave **no** va a
  ningún destino: se enmascara antes de tocar la base y el log. Un mensaje con
  un proyecto confidencial no se escribe en ningún lado.

Cada evento lleva el `turno_id`, de modo que un turno se reconstruye filtrando
por ese campo (`grep turno=t-3f9a` o `jq 'select(.turno=="t-3f9a")'`).
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app import config

_CANDADO = threading.Lock()


# ---------------------------------------------------------------------------
# Turno
# ---------------------------------------------------------------------------
def nuevo_turno() -> str:
    """Identificador corto y legible para seguir un turno en el log."""
    return f"t-{uuid.uuid4().hex[:6]}"


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------
def _permite(nivel: str) -> bool:
    return config.NIVELES.get(nivel, 20) >= config.NIVELES.get(config.LOG_NIVEL, 20)


def _rotar(ruta: Path) -> None:
    """Si el archivo pasa del límite, se guarda como .1 y se empieza de cero."""
    limite = int(config.LOG_ROTACION_MB * 1024 * 1024)
    try:
        if ruta.exists() and ruta.stat().st_size >= limite:
            anterior = ruta.with_suffix(ruta.suffix + ".1")
            if anterior.exists():
                anterior.unlink()
            ruta.rename(anterior)
    except OSError:
        pass                      # nunca tumbar un turno por el log


def evento(nivel: str, nombre: str, turno: str = "", **campos: Any) -> dict[str, Any]:
    """Registra un evento. Devuelve el dict para poder reutilizarlo en pruebas."""
    nivel = nivel.upper()
    if not _permite(nivel):
        return {"nivel": nivel, "evento": nombre, "turno": turno, **campos}

    registro = {
        "ts": datetime.now().isoformat(timespec="milliseconds"),
        "nivel": nivel,
        "evento": nombre,
        "turno": turno,
        **campos,
    }

    if config.LOG_CONSOLA:
        detalle = " ".join(f"{k}={v}" for k, v in campos.items() if v not in (None, ""))
        hora = registro["ts"][11:23]
        print(f"{hora} {nivel:<5} {nombre:<20} turno={turno or '-'} {detalle}".rstrip(),
              flush=True)

    if config.LOG_ARCHIVO:
        with _CANDADO:
            try:
                config.LOG_ARCHIVO.parent.mkdir(parents=True, exist_ok=True)
                _rotar(config.LOG_ARCHIVO)
                with open(config.LOG_ARCHIVO, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(registro, ensure_ascii=False, default=str) + "\n")
            except OSError:
                pass
    return registro


# Azúcar para no repetir el nivel en cada llamada
def info(nombre: str, turno: str = "", **campos: Any) -> dict[str, Any]:
    return evento("INFO", nombre, turno, **campos)


def aviso(nombre: str, turno: str = "", **campos: Any) -> dict[str, Any]:
    return evento("WARN", nombre, turno, **campos)


def error(nombre: str, turno: str = "", **campos: Any) -> dict[str, Any]:
    return evento("ERROR", nombre, turno, **campos)


def depuracion(nombre: str, turno: str = "", **campos: Any) -> dict[str, Any]:
    return evento("DEBUG", nombre, turno, **campos)


# ---------------------------------------------------------------------------
# Lectura (lo que consume el panel del front y `/api/logs`)
# ---------------------------------------------------------------------------
def leer(limite: int = 100, nivel: str | None = None, turno: str | None = None) -> list[dict]:
    """Últimas líneas del archivo, filtradas por nivel y/o turno."""
    ruta = config.LOG_ARCHIVO
    if not ruta or not Path(ruta).exists():
        return []

    with _CANDADO:
        try:
            with open(ruta, encoding="utf-8") as fh:
                lineas = fh.readlines()[-2000:]
        except OSError:
            return []

    salida: list[dict] = []
    piso = config.NIVELES.get((nivel or "").upper(), 0)
    for linea in reversed(lineas):
        linea = linea.strip()
        if not linea:
            continue
        try:
            registro = json.loads(linea)
        except json.JSONDecodeError:
            continue
        if piso and config.NIVELES.get(str(registro.get("nivel")), 0) < piso:
            continue
        if turno and registro.get("turno") != turno:
            continue
        salida.append(registro)
        if len(salida) >= max(1, limite):
            break
    return salida


def limpiar() -> None:
    """Vacía el archivo (lo usan las pruebas)."""
    for ruta in (config.LOG_ARCHIVO, Path(str(config.LOG_ARCHIVO) + ".1")):
        try:
            if ruta and Path(ruta).exists():
                Path(ruta).unlink()
        except OSError:
            pass


__all__ = ["nuevo_turno", "evento", "info", "aviso", "error", "depuracion", "leer", "limpiar"]
