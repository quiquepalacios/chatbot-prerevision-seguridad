"""faq.py — clasificador de preguntas frecuentes del proceso (sesión 5 del curso).

    pregunta corta -> TF-IDF (palabras 1-2 + caracteres 3-5) -> similitud con la base
                   -> >= FAQ_UMBRAL y con margen sobre la segunda: respuesta fija
                   -> si no: sigue al modelo

Cambios respecto del código base:
- Solo se clasifican mensajes cortos (FAQ_MAX_PALABRAS). Una arquitectura de dos
  páginas que menciona "BOLA" no es la pregunta "¿qué es BOLA?".
- Sin "tema crítico -> escalar a un humano": aquí lo crítico (aprobar, código,
  operaciones de cliente) lo resuelven las guardias de alcance.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.pipeline import FeatureUnion

from app import config

FAQ_MAX_PALABRAS = 25


@dataclass(frozen=True)
class FAQ:
    id: str
    categoria: str
    pregunta: str
    variaciones: tuple[str, ...]
    respuesta: str


@dataclass
class Decision:
    ruta: Literal["faq", "modelo"]
    similitud: float
    similitud_segunda: float
    faq: FAQ | None
    motivo: str
    candidatos: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"ruta": self.ruta, "similitud": round(self.similitud, 4),
                "similitud_segunda": round(self.similitud_segunda, 4),
                "faq_id": self.faq.id if self.faq else None, "motivo": self.motivo,
                "candidatos": self.candidatos}


def cargar_faqs(ruta: Path | None = None) -> list[FAQ]:
    datos = json.loads(Path(ruta or config.RUTA_FAQ).read_text("utf-8"))
    return [FAQ(f["id"], f.get("categoria", ""), f["pregunta"], tuple(f.get("variaciones", [])),
                f["respuesta"]) for f in datos["faqs"]]


class ClasificadorFAQ:
    def __init__(self, faqs: list[FAQ] | None = None, umbral: float | None = None,
                 margen: float | None = None, max_palabras: int = FAQ_MAX_PALABRAS) -> None:
        self.faqs = faqs if faqs is not None else cargar_faqs()
        self.umbral = config.FAQ_UMBRAL if umbral is None else umbral
        self.margen = config.FAQ_MARGEN if margen is None else margen
        self.max_palabras = max_palabras
        self.textos: list[str] = []
        self.duenos: list[FAQ] = []
        for faq in self.faqs:
            for texto in (faq.pregunta, *faq.variaciones):
                self.textos.append(texto)
                self.duenos.append(faq)
        # quien escribe sin tildes no debe caer al modelo: strip_accents
        self._union = FeatureUnion([
            ("palabras", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True,
                                         strip_accents="unicode")),
            ("caracteres", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5),
                                           sublinear_tf=True, strip_accents="unicode")),
        ])
        self._matriz = self._union.fit_transform(self.textos) if self.textos else None

    def _puntajes(self, texto: str) -> list[tuple[FAQ, float]]:
        if self._matriz is None:
            return []
        sims = cosine_similarity(self._union.transform([texto]), self._matriz)[0]
        mejores: dict[str, tuple[FAQ, float]] = {}
        for faq, sim in zip(self.duenos, sims):
            if sim > mejores.get(faq.id, (faq, -1.0))[1]:
                mejores[faq.id] = (faq, float(sim))
        return sorted(mejores.values(), key=lambda par: par[1], reverse=True)

    def clasificar(self, texto: str) -> Decision:
        palabras = len(texto.split())
        if palabras > self.max_palabras:
            return Decision("modelo", 0.0, 0.0, None,
                            f"mensaje largo ({palabras} palabras): se revisa con el modelo")
        puntajes = self._puntajes(texto)
        if not puntajes:
            return Decision("modelo", 0.0, 0.0, None, "sin FAQs cargadas")
        (mejor_faq, mejor), segunda = puntajes[0], (puntajes[1][1] if len(puntajes) > 1 else 0.0)
        candidatos = [{"faq_id": f.id, "similitud": round(s, 4)} for f, s in puntajes[:3]]
        if mejor >= self.umbral and mejor - segunda >= self.margen:
            return Decision("faq", mejor, segunda, mejor_faq,
                            f"similitud {mejor:.2f} >= {self.umbral:.2f}", candidatos)
        return Decision("modelo", mejor, segunda, None,
                        f"similitud {mejor:.2f} < {self.umbral:.2f} o sin margen", candidatos)

    def info(self) -> dict[str, Any]:
        return {"faqs": len(self.faqs), "textos": len(self.textos), "umbral": self.umbral,
                "margen": self.margen, "max_palabras": self.max_palabras}


__all__ = ["ClasificadorFAQ", "Decision", "FAQ", "cargar_faqs"]
