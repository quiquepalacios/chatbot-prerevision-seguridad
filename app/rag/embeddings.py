"""embeddings.py — vectores locales con granite multilingüe (sin enviar texto afuera).

Las guías están en inglés y las preguntas llegan en español: un modelo
multilingüe pone "¿cómo evito que un cliente vea la cuenta de otro?" cerca de
"Verify that access to an object is restricted to its owner". Los vectores se
calculan aquí y se le pasan a Chroma ya hechos (Chroma no usa su modelo por
defecto), así indexación y consulta usan siempre el mismo modelo.

Cualquier función `list[str] -> np.ndarray` normalizada sirve de embedder: las
pruebas usan una falsa, sin red ni descargas.
"""
from __future__ import annotations

import threading
from functools import lru_cache
from typing import Callable, Sequence

import numpy as np

from app import config

Embedder = Callable[[Sequence[str]], np.ndarray]

_CANDADO = threading.Lock()


@lru_cache(maxsize=1)
def _modelo():
    import torch
    from sentence_transformers import SentenceTransformer

    # Dos hilos cargando el modelo a la vez pueden tumbar el proceso por memoria.
    with _CANDADO:
        return SentenceTransformer(config.MODELO_EMBEDDINGS,
                                   model_kwargs={"dtype": torch.float32})


def embed_granite(textos: Sequence[str], lote: int = 32,
                  progreso: bool = False) -> np.ndarray:
    """Vectores normalizados (coseno = producto punto)."""
    matriz = _modelo().encode(list(textos), normalize_embeddings=True, batch_size=lote,
                              show_progress_bar=progreso)
    return np.asarray(matriz, dtype="float32")


def dimension() -> int:
    modelo = _modelo()
    metodo = (getattr(modelo, "get_embedding_dimension", None)
              or modelo.get_sentence_embedding_dimension)
    return int(metodo())


def modelo_cargado() -> bool:
    return _modelo.cache_info().currsize > 0


__all__ = ["Embedder", "dimension", "embed_granite", "modelo_cargado"]
