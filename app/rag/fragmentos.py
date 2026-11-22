"""fragmentos.py — de bloques limpios a fragmentos citables de ~150 palabras.

Es el troceo de la sesión 6/7 (oraciones agrupadas hasta `CHUNK_MAX_PALABRAS`,
con `CHUNK_OVERLAP_PALABRAS` de solape) con dos cambios:

1. **Oraciones con spaCy multilingüe** (`spacy.blank("xx")` + `sentencizer`): las
   fuentes están en inglés y en español, y el segmentador por reglas no necesita
   descargar ningún modelo. Las abreviaturas ("e.g.", "Sec.", "Dr.") se vuelven a
   unir porque el segmentador las confunde con un final de oración.
2. **Bloques atómicos**: una regla `POL-01.1` o un requisito `ASVS V8.2.1` ya es la
   unidad que se cita, así que pasa entero aunque sea corto (o algo largo).

Cada fragmento lleva los metadatos que necesita la cita: fuente, cita, sección,
página, licencia y, en los lineamientos, la "Revisión obligatoria".
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterable

from app import config
from app.rag.cargadores import Bloque, Documento

MIN_PALABRAS = 12          # un trozo de 3 palabras no responde nada


@dataclass
class Fragmento:
    id: str
    texto: str
    metadatos: dict[str, Any] = field(default_factory=dict)

    @property
    def palabras(self) -> int:
        return len(self.texto.split())

    def texto_para_embedding(self) -> str:
        """Fuente y sección delante del texto: ayudan a recuperar el fragmento
        correcto ("Cómo prevenir" de A01) sin cambiar lo que se muestra.

        En las reglas POL se suman el objetivo y las referencias de su política:
        una regla suelta es corta, y "API1:2023 (BOLA)" la acerca a las preguntas
        que describen ese riesgo con otras palabras."""
        m = self.metadatos
        if m.get("tipo") == "lineamiento":
            partes = [m.get("cita", ""), m.get("seccion", ""), m.get("objetivo", ""),
                      m.get("referencias", "")]
        else:
            partes = [m.get("cita", ""), m.get("seccion", "")]
        cabecera = " — ".join(x for x in partes if x)
        return f"{cabecera}\n{self.texto}" if cabecera else self.texto


# ---------------------------------------------------------------------------
# Oraciones
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _nlp():
    """Segmentador por reglas de spaCy; si spaCy falta, se usa la regex."""
    try:
        import spacy

        nlp = spacy.blank("xx")
        nlp.add_pipe("sentencizer")
        nlp.max_length = 2_000_000
        return nlp
    except Exception:                       # noqa: BLE001 - sin spaCy
        return None


RE_FRONTERA = re.compile(r"(?<=[.!?…])\s+(?=[¿¡A-ZÁÉÍÓÚÑ0-9•\-])")
RE_ABREVIATURA = re.compile(
    r"(?:\b(?:e\.g|i\.e|etc|vs|cf|approx|sec|secs|fig|figs|no|nos|vol|pp|p|min|max|"
    r"dr|dra|sr|sra|srta|ing|lic|prof|s\.a|ltda|n|num|núm|pág|pag|art|inc|cap|ej|aprox)\.)$",
    re.IGNORECASE,
)


def _unir_abreviaturas(oraciones: list[str]) -> list[str]:
    unidas: list[str] = []
    for oracion in oraciones:
        if unidas and RE_ABREVIATURA.search(unidas[-1]):
            unidas[-1] = f"{unidas[-1]} {oracion}"
        else:
            unidas.append(oracion)
    return unidas


def dividir_oraciones(texto: str) -> list[str]:
    nlp = _nlp()
    if nlp is None:
        limpio = " ".join(texto.split())
        return _unir_abreviaturas([o.strip() for o in RE_FRONTERA.split(limpio) if o.strip()])
    return _unir_abreviaturas([o.text.strip() for o in nlp(texto).sents if o.text.strip()])


def trocear(texto: str, max_palabras: int = 0, overlap_palabras: int | None = None) -> list[str]:
    """Agrupa oraciones hasta `max_palabras` y solapa las últimas N palabras."""
    max_palabras = max_palabras or config.CHUNK_MAX_PALABRAS
    overlap_palabras = (config.CHUNK_OVERLAP_PALABRAS if overlap_palabras is None
                        else overlap_palabras)
    chunks: list[str] = []
    actual: list[str] = []
    for oracion in dividir_oraciones(texto):
        palabras = oracion.split()
        if not palabras:
            continue
        if len(palabras) > max_palabras:                 # oración gigante (tablas, listas)
            if actual:
                chunks.append(" ".join(actual))
                actual = []
            paso = max(max_palabras - overlap_palabras, 1)
            for i in range(0, len(palabras), paso):
                chunks.append(" ".join(palabras[i:i + max_palabras]))
                if i + max_palabras >= len(palabras):
                    break
            continue
        if actual and len(actual) + len(palabras) > max_palabras:
            chunks.append(" ".join(actual))
            cola = actual[-overlap_palabras:] if overlap_palabras else []
            while cola and len(cola) + len(palabras) > max_palabras:
                cola = cola[1:]
            actual = list(cola)
        actual.extend(palabras)
    if actual:
        chunks.append(" ".join(actual))
    return chunks


# ---------------------------------------------------------------------------
# Documento -> fragmentos
# ---------------------------------------------------------------------------
def _slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", texto.lower()).strip("-")[:48] or "doc"


def _metadatos(doc: Documento, bloque: Bloque, archivo: str, licencia: str) -> dict[str, Any]:
    """Chroma solo acepta str/int/float/bool: nada de None ni listas."""
    meta: dict[str, Any] = {
        "fuente": doc.fuente,
        "cita": bloque.cita or doc.fuente,
        "titulo": doc.titulo[:200],
        "tipo": doc.tipo,
        "formato": doc.formato,
        "archivo": archivo,
        "seccion": bloque.seccion[:300],
        "pagina": bloque.pagina if bloque.pagina is not None else -1,
        "licencia": licencia,
        "atomico": bloque.atomico,
    }
    for clave, valor in bloque.extra.items():
        if isinstance(valor, (str, int, float, bool)):
            meta[clave] = valor
    if doc.tipo == "lineamiento":
        meta["requiere_revision"] = bool(bloque.extra.get("revision_obligatoria"))
    return meta


def fragmentar(doc: Documento, archivo: str, licencia: str = "",
               max_palabras: int = 0, overlap_palabras: int | None = None) -> list[Fragmento]:
    """Trocea los bloques normales y deja pasar enteros los atómicos."""
    fragmentos: list[Fragmento] = []
    base = _slug(f"{doc.tipo}-{doc.fuente}")
    for bloque in doc.bloques:
        meta = _metadatos(doc, bloque, archivo, licencia)
        if bloque.atomico:
            trozos = [bloque.texto]
        else:
            trozos = [t for t in trocear(bloque.texto, max_palabras, overlap_palabras)
                      if len(t.split()) >= MIN_PALABRAS]
        for trozo in trozos:
            ident = (f"{_slug(bloque.cita)}" if bloque.atomico and bloque.cita
                     else f"{base}-{len(fragmentos):04d}")
            fragmentos.append(Fragmento(ident, trozo, dict(meta)))
    return fragmentos


def sin_repetidos(fragmentos: Iterable[Fragmento]) -> tuple[list[Fragmento], int]:
    """Quita fragmentos con el mismo texto (entre documentos) e ids repetidos."""
    vistos_texto: set[str] = set()
    vistos_id: dict[str, int] = {}
    salida: list[Fragmento] = []
    quitados = 0
    for f in fragmentos:
        huella = hashlib.sha1(" ".join(f.texto.lower().split()).encode()).hexdigest()
        if huella in vistos_texto:
            quitados += 1
            continue
        vistos_texto.add(huella)
        if f.id in vistos_id:                       # dos documentos con la misma cita
            vistos_id[f.id] += 1
            f = Fragmento(f"{f.id}-{vistos_id[f.id]}", f.texto, f.metadatos)
        else:
            vistos_id[f.id] = 0
        salida.append(f)
    return salida, quitados


__all__ = ["Fragmento", "MIN_PALABRAS", "dividir_oraciones", "fragmentar", "sin_repetidos",
           "trocear"]
