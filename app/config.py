"""config.py — un solo sitio para leer el `.env` y los umbrales del producto.

Nada de valores mágicos repartidos por el código: si un número se puede ajustar,
se lee aquí y se documenta en `.env.example` y en el README.

Las rutas relativas del `.env` se resuelven contra la raíz del repositorio, así
el servidor, los scripts y las pruebas funcionan desde cualquier carpeta.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]          # raíz del repositorio
APP_DIR = BASE_DIR / "app"
load_dotenv(BASE_DIR / ".env")

# sentence-transformers intenta importar TensorFlow si lo encuentra y con
# Keras 3 falla. Se apaga antes de que cualquier módulo lo importe.
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
# Chroma no envía telemetría (también se apaga en el cliente).
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

# Certificados del sistema operativo para HTTPS (OpenAI, Hugging Face, OWASP,
# NIST). En equipos con antivirus o proxy que inspeccionan TLS, el paquete de
# certificados de Python no reconoce la CA local y toda conexión falla con
# CERTIFICATE_VERIFY_FAILED; Windows sí la reconoce. La verificación no se
# apaga: solo cambia de qué almacén se leen las CA. SSL_SISTEMA=0 lo desactiva.
if os.getenv("SSL_SISTEMA", "1").strip().lower() not in {"0", "false", "no", "off"}:
    try:
        import truststore

        truststore.inject_into_ssl()
    except ImportError:                      # sin truststore: certificados de certifi
        pass


def _num(nombre: str, por_defecto: float) -> float:
    try:
        return float(os.getenv(nombre, por_defecto))
    except (TypeError, ValueError):
        return float(por_defecto)


def _ent(nombre: str, por_defecto: int) -> int:
    return int(_num(nombre, por_defecto))


def _bool(nombre: str, por_defecto: bool = False) -> bool:
    valor = os.getenv(nombre)
    if valor is None:
        return por_defecto
    return valor.strip().lower() in {"1", "true", "si", "sí", "yes", "on"}


def _ruta(nombre: str, por_defecto: str) -> Path:
    ruta = Path(os.getenv(nombre, por_defecto))
    return ruta if ruta.is_absolute() else BASE_DIR / ruta


# -- rutas -------------------------------------------------------------------
CARPETA_CONOCIMIENTO = _ruta("CONOCIMIENTO_DIR", "conocimiento")
CARPETA_LINEAMIENTOS = CARPETA_CONOCIMIENTO / "lineamientos"   # en el repo
CARPETA_FUENTES = CARPETA_CONOCIMIENTO / "fuentes"             # originales, tal cual llegaron
CARPETA_PROCESADO = CARPETA_CONOCIMIENTO / "procesado"         # datos limpios (capa 2)
CARPETA_CASOS = _ruta("CASOS_DIR", "casos_prueba")
RUTA_DB = _ruta("DB_PATH", "data/revisiones.db")
CARPETA_LOGS = _ruta("LOG_DIR", "data/logs")
CARPETA_INFORMES = _ruta("INFORMES_DIR", "data/informes")
RUTA_FAQ = APP_DIR / "servicios" / "faq.json"
RUTA_TERMINOS = _ruta("TERMINOS_PATH", "app/seguridad/terminos_bloqueados.json")

# -- base vectorial (Chroma persistente) --------------------------------------
CHROMA_DIR = _ruta("CHROMA_DIR", "chroma_db")
CHROMA_COLECCION = os.getenv("CHROMA_COLECCION", "lineamientos")

# -- clasificador de FAQs ------------------------------------------------------
FAQ_UMBRAL = _num("FAQ_UMBRAL", 0.55)
FAQ_MARGEN = _num("FAQ_MARGEN", 0.03)
FAQ_CONTEXTO_MIN = _num("FAQ_CONTEXTO_MIN", 0.25)

# -- recuperación de lineamientos (top-k -> MMR -> prompt) ---------------------
# granite da similitudes altas y juntas (0.74-0.91): el 0.35 del curso no filtra
# nada. 0.79 sale de las preguntas de control (docs/reporte_indice.md).
RAG_UMBRAL = _num("RAG_UMBRAL", 0.79)
RAG_K_INICIAL = _ent("RAG_K_INICIAL", 8)      # cuántos se recuperan antes del MMR
RAG_K_FINAL = _ent("RAG_K_FINAL", 3)          # cuántos llegan al prompt
RAG_MMR_LAMBDA = _num("RAG_MMR_LAMBDA", 0.5)  # 1 = solo relevancia, 0 = solo novedad
CHUNK_MAX_PALABRAS = _ent("CHUNK_MAX_PALABRAS", 150)
CHUNK_OVERLAP_PALABRAS = _ent("CHUNK_OVERLAP_PALABRAS", 25)
MODELO_EMBEDDINGS = os.getenv(
    "SENTENCE_TRANSFORMER_MODEL", "ibm-granite/granite-embedding-97m-multilingual-r2"
)

# -- conversación ------------------------------------------------------------
MAX_MENSAJES = _ent("MAX_MENSAJES", 8)        # ventana de memoria por sesión
# buscar (varias) -> registrar -> escalar -> responder: 6 rondas sobran
MAX_VUELTAS = _ent("MAX_VUELTAS", 6)          # rondas de herramientas por turno
# Una arquitectura descrita por un líder técnico ocupa varias páginas: el
# límite es mayor que el del chat de la tienda (2000).
LIMITE_MENSAJE = _ent("LIMITE_MENSAJE", 12000)
LLM_TEMPERATURA = _num("LLM_TEMPERATURA", 0.2)
# registrar_revision con ~20 hallazgos en JSON ocupa ~3000 tokens de salida y, en los
# modelos de razonamiento (gpt-5.x, gpt-6.x), el límite incluye lo que el modelo piensa
LLM_MAX_TOKENS = _ent("LLM_MAX_TOKENS", 16000)
RETENCION_DIAS = _ent("DB_RETENCION_DIAS", 90)

# -- logs ----------------------------------------------------------------------
LOG_CONSOLA = _bool("LOG_CONSOLA", True)
LOG_ARCHIVO = CARPETA_LOGS / os.getenv("LOG_ARCHIVO_NOMBRE", "eventos.jsonl")
LOG_NIVEL = os.getenv("LOG_NIVEL", "INFO").upper()
LOG_ROTACION_MB = _num("LOG_ROTACION_MB", 2.0)
LLM_LENTO_MS = _ent("LLM_LENTO_MS", 15000)

NIVELES = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}

# -- servidor ------------------------------------------------------------------
PUERTO = _ent("PORT", 8040)

AVISO_SEGURIDAD = (
    "Esta es una pre-revisión automática: no reemplaza la revisión ni la "
    "aprobación del área de Arquitectura de Seguridad."
)


def resumen() -> dict[str, object]:
    """Lo que `/api/health` enseña y el README documenta."""
    return {
        "faq_umbral": FAQ_UMBRAL,
        "faq_margen": FAQ_MARGEN,
        "rag_umbral": RAG_UMBRAL,
        "rag_k": [RAG_K_INICIAL, RAG_K_FINAL],
        "rag_mmr_lambda": RAG_MMR_LAMBDA,
        "chunk": [CHUNK_MAX_PALABRAS, CHUNK_OVERLAP_PALABRAS],
        "embeddings": MODELO_EMBEDDINGS,
        "chroma": {"carpeta": str(CHROMA_DIR.relative_to(BASE_DIR))
                   if CHROMA_DIR.is_relative_to(BASE_DIR) else str(CHROMA_DIR),
                   "coleccion": CHROMA_COLECCION},
        "max_mensajes": MAX_MENSAJES,
        "log_nivel": LOG_NIVEL,
        "llm_lento_ms": LLM_LENTO_MS,
    }
