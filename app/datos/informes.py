"""informes.py — exporta una revisión a Markdown (para leer) o CSV (para planillas).

El informe repite lo esencial del flujo: cada hallazgo con severidad, fuente y
si va a Seguridad; las preguntas pendientes; los escalamientos; y el aviso de
que no reemplaza la revisión del área de Seguridad.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any

from app import config

FORMATOS = ("md", "csv")
COLUMNAS = ["revision_id", "solucion", "titulo", "estado", "severidad", "nivel", "requiere_seguridad",
            "fuentes", "descripcion", "recomendacion", "verificar"]
ESTADO_TEXTO = {"confirmado": "confirmado", "a_confirmar": "a confirmar"}


def _md(revision: dict[str, Any]) -> str:
    hallazgos = revision["hallazgos"]
    a_seguridad = [h for h in hallazgos if h["requiere_seguridad"]]
    lineas = [
        f"# Pre-revisión de seguridad {revision['id']}",
        "",
        f"- **Solución:** {revision['solucion']}",
        f"- **Estado:** {revision['estado']}",
        f"- **Actualizada:** {revision['actualizada']}",
        f"- **Hallazgos:** {len(hallazgos)} ({len(a_seguridad)} van a Arquitectura de Seguridad; "
        f"{sum(h.get('estado') == 'a_confirmar' for h in hallazgos)} a confirmar con el equipo)",
        "",
        f"> {config.AVISO_SEGURIDAD}",
        "",
    ]
    if revision.get("resumen"):
        lineas += ["## Resumen", "", revision["resumen"], ""]
    lineas += ["## Hallazgos", ""]
    if not hallazgos:
        lineas += ["Sin hallazgos registrados.", ""]
    for n, h in enumerate(hallazgos, 1):
        marca = " · **va a Seguridad**" if h["requiere_seguridad"] else ""
        lineas += [
            f"### {n}. {h['titulo']}",
            "",
            f"**{ESTADO_TEXTO.get(h.get('estado'), 'a confirmar').capitalize()}** · severidad "
            f"**{h['severidad']}** · nivel {h['nivel']}{marca}",
            "",
            f"- **Descripción:** {h.get('descripcion') or '—'}",
            f"- **Recomendación:** {h.get('recomendacion') or '—'}",
            f"- **Fuentes:** {', '.join(h['fuentes'])}",
        ]
        if h.get("verificar"):
            lineas.append(f"- **Qué confirmar:** {h['verificar']}")
        lineas.append("")
    if revision.get("preguntas"):
        lineas += ["## Preguntas pendientes", ""] + [f"- {p}" for p in revision["preguntas"]] + [""]
    if revision.get("escalamientos"):
        lineas += ["## Derivado a Arquitectura de Seguridad", ""]
        for e in revision["escalamientos"]:
            lineas.append(f"- **{e['id']}** ({e['creado']}): {e['motivo']}")
            lineas += [f"  - {p}" for p in e["puntos"]]
        lineas.append("")
    return "\n".join(lineas)


def _csv(revision: dict[str, Any]) -> str:
    salida = io.StringIO()
    escritor = csv.DictWriter(salida, fieldnames=COLUMNAS, lineterminator="\n")
    escritor.writeheader()
    for h in revision["hallazgos"]:
        escritor.writerow({
            "revision_id": revision["id"], "solucion": revision["solucion"],
            "titulo": h["titulo"], "estado": ESTADO_TEXTO.get(h.get("estado"), "a confirmar"),
            "severidad": h["severidad"], "nivel": h["nivel"],
            "requiere_seguridad": "si" if h["requiere_seguridad"] else "no",
            "fuentes": "; ".join(h["fuentes"]),
            "descripcion": h.get("descripcion") or "", "recomendacion": h.get("recomendacion") or "",
            "verificar": h.get("verificar") or "",
        })
    return salida.getvalue()


def generar(revision: dict[str, Any], formato: str = "md") -> str:
    if formato not in FORMATOS:
        raise ValueError(f"formato no soportado: {formato} (usa {', '.join(FORMATOS)})")
    return _md(revision) if formato == "md" else _csv(revision)


def nombre_archivo(revision_id: str, formato: str) -> str:
    if not re.fullmatch(r"REV-[0-9A-F]{6}", revision_id or ""):
        raise ValueError("id de revisión inválido")
    if formato not in FORMATOS:
        raise ValueError(f"formato no soportado: {formato}")
    return f"{revision_id}.{formato}"


def exportar(revision: dict[str, Any], formato: str = "md", carpeta: Path | None = None) -> Path:
    """Escribe el informe en data/informes/ y devuelve la ruta."""
    carpeta = Path(carpeta or config.CARPETA_INFORMES)
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / nombre_archivo(revision["id"], formato)
    # utf-8-sig: Excel abre el CSV con las tildes bien
    ruta.write_text(generar(revision, formato), encoding="utf-8-sig" if formato == "csv" else "utf-8")
    return ruta


__all__ = ["FORMATOS", "exportar", "generar", "nombre_archivo"]
