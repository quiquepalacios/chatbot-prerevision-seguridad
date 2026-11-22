"""procesado.py — la capa de datos limpios, guardada aparte del original y del índice.

El conocimiento se guarda en tres capas (como recomendó el docente):

    1. originales   conocimiento/lineamientos y conocimiento/fuentes: los archivos tal
                    cual llegaron (PDF, CSV, Markdown), con su SHA-256 en el manifiesto
    2. limpios      conocimiento/procesado: un JSON por documento con el texto ya limpio,
                    dividido en bloques con sus metadatos, y qué se le quitó
    3. fragmentos   chroma_db: los fragmentos de ~150 palabras con sus embeddings

El índice (capa 3) se construye SIEMPRE a partir de la capa 2, nunca directo del
original: lo que está en `procesado/` es exactamente lo que recibe el RAG, y se
puede abrir y revisar sin ejecutar nada. Cada JSON guarda el SHA-256 del original:
si el original cambia, se vuelve a procesar; si no, se reutiliza.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from app.rag.cargadores import Bloque, Documento

VERSION_FORMATO = 1


def sha256(ruta: Path) -> str:
    return hashlib.sha256(Path(ruta).read_bytes()).hexdigest()


def ruta_para(carpeta_procesado: Path, relativo: str) -> Path:
    """`fuentes/nist/NIST.SP.800-207.pdf` -> `procesado/fuentes/nist/NIST.SP.800-207.pdf.json`."""
    return Path(carpeta_procesado) / f"{relativo}.json"


def guardar(doc: Documento, destino: Path, relativo: str, huella: str, licencia: str) -> Path:
    datos: dict[str, Any] = {
        "version_formato": VERSION_FORMATO,
        "original": relativo,
        "sha256_original": huella,
        "licencia": licencia,
        "procesado": datetime.now().isoformat(timespec="seconds"),
        "fuente": doc.fuente,
        "titulo": doc.titulo,
        "tipo": doc.tipo,
        "formato": doc.formato,
        "palabras": doc.palabras,
        "limpieza": dict(sorted(doc.limpieza.items())),
        "bloques": [{k: v for k, v in asdict(b).items() if v not in ("", None, {}, False)}
                    for b in doc.bloques],
    }
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_suffix(destino.suffix + ".parcial")
    temporal.write_text(json.dumps(datos, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    temporal.replace(destino)
    return destino


def leer(ruta: Path) -> tuple[Documento, dict[str, Any]]:
    """El documento limpio tal como lo guarda `guardar`, más sus metadatos."""
    datos = json.loads(Path(ruta).read_text("utf-8"))
    bloques = [Bloque(texto=b["texto"], seccion=b.get("seccion", ""), pagina=b.get("pagina"),
                      cita=b.get("cita", ""), atomico=b.get("atomico", False),
                      extra=b.get("extra", {})) for b in datos["bloques"]]
    doc = Documento(ruta=Path(datos["original"]), fuente=datos["fuente"], titulo=datos["titulo"],
                    tipo=datos["tipo"], formato=datos["formato"], bloques=bloques,
                    limpieza=Counter(datos.get("limpieza", {})))
    return doc, datos


def vigente(ruta: Path, huella: str) -> bool:
    """True si el JSON existe, tiene el formato actual y corresponde al mismo original."""
    try:
        datos = json.loads(Path(ruta).read_text("utf-8"))
    except (OSError, ValueError):
        return False
    return (datos.get("version_formato") == VERSION_FORMATO
            and datos.get("sha256_original") == huella)


def huerfanos(carpeta_procesado: Path, vigentes: set[Path]) -> list[Path]:
    """JSON de documentos que ya no están (o ya no se indexan)."""
    carpeta = Path(carpeta_procesado)
    if not carpeta.exists():
        return []
    usados = {p.resolve() for p in vigentes}
    return sorted(p for p in carpeta.rglob("*.json") if p.resolve() not in usados)


__all__ = ["VERSION_FORMATO", "guardar", "huerfanos", "leer", "ruta_para", "sha256", "vigente"]
