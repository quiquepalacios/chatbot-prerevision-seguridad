"""indice.py — base vectorial Chroma persistente + búsqueda con MMR.

    pregunta ──embedding──> Chroma (coseno) ──top RAG_K_INICIAL──> umbral
             ──> MMR (relevancia vs. novedad) ──> RAG_K_FINAL fragmentos al prompt

- **Persistente**: `scripts/indexar.py` construye el índice una vez; el servidor
  solo lo abre y arranca al instante (no reindexa miles de fragmentos).
- **Reconstrucción segura**: se indexa en una colección temporal y solo al
  terminar reemplaza a la vigente. Si la indexación falla, el chat sigue con el
  índice anterior.
- **MMR** con los vectores que devuelve Chroma: sin MMR, tres fragmentos casi
  iguales del mismo párrafo ocupan todo el contexto.
- **Umbral explícito**: si nada supera `RAG_UMBRAL`, no se inyecta contexto. Es
  mejor decir "no encontré un lineamiento" que citar uno que no responde.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from app import config
from app.rag.embeddings import Embedder, embed_granite
from app.rag.fragmentos import Fragmento

SUFIJO_TEMPORAL = "__nuevo"
LOTE = 128


def cliente_chroma(carpeta: Path | None = None):
    """Cliente persistente con la telemetría apagada."""
    import chromadb
    from chromadb.config import Settings

    carpeta = Path(carpeta or config.CHROMA_DIR)
    carpeta.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(carpeta),
                                     settings=Settings(anonymized_telemetry=False,
                                                       allow_reset=False))


@dataclass
class Resultado:
    id: str
    texto: str
    metadatos: dict[str, Any]
    score: float                       # similitud coseno con la pregunta

    @property
    def cita(self) -> str:
        return str(self.metadatos.get("cita", ""))

    def to_dict(self) -> dict[str, Any]:
        m = self.metadatos
        return {
            "id": self.id, "cita": self.cita, "fuente": m.get("fuente", ""),
            "seccion": m.get("seccion", ""),
            "pagina": m.get("pagina") if m.get("pagina", -1) != -1 else None,
            "revision_obligatoria": m.get("revision_obligatoria", ""),
            "texto": self.texto, "score": round(self.score, 4),
        }


class IndiceLineamientos:
    """Envoltorio de la colección: construir, abrir y buscar."""

    def __init__(self, carpeta: Path | None = None, coleccion: str | None = None,
                 embedder: Embedder | None = None, cliente: Any = None) -> None:
        self.carpeta = Path(carpeta or config.CHROMA_DIR)
        self.nombre = coleccion or config.CHROMA_COLECCION
        self.embedder = embedder or embed_granite
        self._cliente = cliente
        self._coleccion = None
        self.aviso = ""

    # -- acceso ------------------------------------------------------------
    @property
    def cliente(self):
        if self._cliente is None:
            self._cliente = cliente_chroma(self.carpeta)
        return self._cliente

    def coleccion(self):
        """La colección vigente, o None si todavía no se indexó."""
        if self._coleccion is None:
            try:
                self._coleccion = self.cliente.get_collection(self.nombre)
            except Exception:                  # noqa: BLE001 - no existe todavía
                self.aviso = ("No hay índice de lineamientos: ejecuta "
                              "'python -m scripts.indexar'.")
                return None
        return self._coleccion

    def disponible(self) -> bool:
        col = self.coleccion()
        return col is not None and col.count() > 0

    def info(self) -> dict[str, Any]:
        col = self.coleccion()
        if col is None:
            return {"indexado": False, "fragmentos": 0, "aviso": self.aviso}
        meta = dict(col.metadata or {})
        return {"indexado": True, "fragmentos": col.count(),
                "coleccion": self.nombre, **{k: v for k, v in meta.items()
                                             if not k.startswith("hnsw:")}}

    # -- construcción --------------------------------------------------------
    def construir(self, fragmentos: Sequence[Fragmento], metadatos: dict[str, Any] | None = None,
                  avisar=print) -> int:
        """Indexa en una colección temporal y la pone en lugar de la vigente."""
        temporal = self.nombre + SUFIJO_TEMPORAL
        try:
            self.cliente.delete_collection(temporal)        # restos de un intento fallido
        except Exception:                                    # noqa: BLE001
            pass
        meta = {"hnsw:space": "cosine", "creado": time.strftime("%Y-%m-%d %H:%M:%S"),
                "embeddings": config.MODELO_EMBEDDINGS}
        meta.update({k: v for k, v in (metadatos or {}).items()
                     if isinstance(v, (str, int, float, bool))})
        col = self.cliente.create_collection(temporal, metadata=meta,
                                             embedding_function=None)
        total = len(fragmentos)
        for inicio in range(0, total, LOTE):
            lote = fragmentos[inicio:inicio + LOTE]
            vectores = self.embedder([f.texto_para_embedding() for f in lote])
            col.add(ids=[f.id for f in lote], documents=[f.texto for f in lote],
                    metadatas=[f.metadatos for f in lote],
                    embeddings=np.asarray(vectores, dtype="float32").tolist())
            avisar(f"  {min(inicio + LOTE, total)}/{total} fragmentos")

        # Cambio: la colección nueva toma el nombre de la vigente.
        try:
            self.cliente.delete_collection(self.nombre)
        except Exception:                                    # noqa: BLE001 - primera vez
            pass
        col.modify(name=self.nombre)
        self._coleccion = None
        return total

    # -- búsqueda ------------------------------------------------------------
    def buscar(self, pregunta: str, k: int | None = None, inicial: int | None = None,
               lam: float | None = None, umbral: float | None = None,
               donde: dict[str, Any] | None = None,
               garantizar_interna: bool = True) -> list[Resultado]:
        """Top-`inicial` por coseno, filtro por umbral, MMR hasta `k` y, si no quedó
        ninguna, la regla interna POL más cercana (ver `_con_regla_interna`)."""
        col = self.coleccion()
        if col is None or not pregunta.strip():
            return []
        k = k or config.RAG_K_FINAL
        inicial = max(inicial or config.RAG_K_INICIAL, k)
        lam = config.RAG_MMR_LAMBDA if lam is None else lam
        umbral = config.RAG_UMBRAL if umbral is None else umbral

        consulta = np.asarray(self.embedder([pregunta]), dtype="float32")[0]
        r = col.query(query_embeddings=[consulta.tolist()], n_results=inicial, where=donde,
                      include=["documents", "metadatas", "distances", "embeddings"])
        if not r["ids"] or not r["ids"][0]:
            return []
        candidatos = [
            Resultado(i, d, m or {}, 1.0 - float(dist))
            for i, d, m, dist in zip(r["ids"][0], r["documents"][0], r["metadatas"][0],
                                     r["distances"][0])
        ]
        vectores = np.asarray(r["embeddings"][0], dtype="float32")
        validos = [n for n, c in enumerate(candidatos) if c.score >= umbral]
        if not validos:
            return []
        elegidos = [candidatos[n] for n in mmr(vectores, [c.score for c in candidatos],
                                               validos, k, lam)]
        if garantizar_interna and donde is None:
            elegidos = self._con_regla_interna(elegidos, consulta, umbral)
        return elegidos

    def _con_regla_interna(self, elegidos: list[Resultado], consulta: np.ndarray,
                           umbral: float) -> list[Resultado]:
        """Toda observación debe citar la regla POL que incumple (LSA-2026). Si el
        MMR no dejó ninguna, la mejor regla interna sobre el umbral reemplaza al
        último fragmento: siguen llegando `k` fragmentos al prompt."""
        # con un solo fragmento no se reemplaza: se perdería el único relevante
        if len(elegidos) < 2 or any(e.metadatos.get("tipo") == "lineamiento" for e in elegidos):
            return elegidos
        r = self.coleccion().query(query_embeddings=[consulta.tolist()], n_results=1,
                                   where={"tipo": "lineamiento"},
                                   include=["documents", "metadatas", "distances"])
        if not r["ids"] or not r["ids"][0]:
            return elegidos
        regla = Resultado(r["ids"][0][0], r["documents"][0][0], r["metadatas"][0][0] or {},
                          1.0 - float(r["distances"][0][0]))
        if regla.score < umbral:
            return elegidos
        regla.metadatos = {**regla.metadatos, "agregada": "regla interna garantizada"}
        return elegidos[:-1] + [regla]

    def contexto(self, resultados: Sequence[Resultado]) -> str:
        """Texto para el prompt: cada fragmento con su cita entre corchetes."""
        partes = []
        for res in resultados:
            m = res.metadatos
            cabecera = f"[{res.cita}]"
            if m.get("seccion"):
                cabecera += f" {m['seccion']}"
            if m.get("revision_obligatoria"):
                cabecera += f" | Revisión obligatoria: {m['revision_obligatoria']}"
            partes.append(f"{cabecera}\n{res.texto}")
        return "\n\n".join(partes)


def mmr(vectores: np.ndarray, scores: Sequence[float], candidatos: Sequence[int],
        k: int, lam: float) -> list[int]:
    """Maximal Marginal Relevance: lam·relevancia − (1−lam)·parecido con lo elegido."""
    restantes = list(candidatos)
    elegidos: list[int] = []
    normas = vectores / np.clip(np.linalg.norm(vectores, axis=1, keepdims=True), 1e-9, None)
    while restantes and len(elegidos) < k:
        if not elegidos:
            mejor = max(restantes, key=lambda i: scores[i])
        else:
            def puntuar(i: int) -> float:
                redundancia = max(float(normas[i] @ normas[j]) for j in elegidos)
                return lam * scores[i] - (1 - lam) * redundancia

            mejor = max(restantes, key=puntuar)
        elegidos.append(mejor)
        restantes.remove(mejor)
    return elegidos


def guardar_reporte(reporte: dict[str, Any], carpeta: Path | None = None) -> Path:
    ruta = Path(carpeta or config.CHROMA_DIR) / "reporte_indice.json"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(reporte, ensure_ascii=False, indent=2) + "\n", "utf-8")
    return ruta


__all__ = ["IndiceLineamientos", "Resultado", "cliente_chroma", "guardar_reporte", "mmr"]
