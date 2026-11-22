"""citas.py — qué cuenta como fuente válida de un hallazgo.

Un hallazgo sin fuente, o con una fuente inventada ("POL-99.9", "API12:2023"),
no llega al líder técnico. Se reconocen los formatos de cita del corpus:

    POL-01.1 · POL-01           lineamientos internos LSA-2026 (deben existir)
    ASVS V8.2.1 · V8.2.1        OWASP ASVS 5.0
    API1:2023 … API10:2023      OWASP API Security Top 10
    A01:2025 … A10:2025         OWASP Top 10:2025
    NIST SP 800-207             publicaciones NIST indexadas
    OWASP Cheat Sheet: …        cheat sheets
    PCI DSS · SBS 504-2021 · Ley 29733   marcos regulatorios citados en LSA-2026
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from app import config

RE_POL = re.compile(r"\bPOL-(\d{2})(?:\.(\d{1,2}))?\b", re.IGNORECASE)
RE_ASVS = re.compile(r"\b(?:ASVS\s*(?:5\.0\s*)?)?V(\d{1,2})\.(\d{1,2})\.(\d{1,2})\b", re.IGNORECASE)
RE_API = re.compile(r"\bAPI(\d{1,2}):2023\b", re.IGNORECASE)
RE_TOP10 = re.compile(r"\bA(\d{2}):2025\b", re.IGNORECASE)
RE_NIST = re.compile(r"\bNIST\s*SP\s*800-(204|207|218|63B(?:-4)?)\b", re.IGNORECASE)
RE_CHEAT = re.compile(r"\bOWASP\s+Cheat\s+Sheet\b|\bCheat\s+Sheet\b", re.IGNORECASE)
RE_REGULATORIO = re.compile(r"\bPCI[\s-]?DSS\b|\bSBS\s*(N\.?\s*°?\s*)?504-2021\b|\bLey\s*(N\.?\s*°?\s*)?29733\b",
                            re.IGNORECASE)


@lru_cache(maxsize=1)
def reglas_internas(ruta: Path | None = None) -> frozenset[str]:
    """IDs POL-XX.N que existen en el documento LSA-2026 del repositorio."""
    ruta = ruta or (config.CARPETA_LINEAMIENTOS / "politicas_seguridad_banco_andesur.md")
    try:
        texto = ruta.read_text("utf-8")
    except OSError:
        return frozenset()
    reglas = re.findall(r"^-\s+\*\*(POL-\d{2}\.\d+)\*\*", texto, flags=re.MULTILINE)
    return frozenset(reglas)


def politicas_internas() -> frozenset[str]:
    return frozenset(r.split(".")[0] for r in reglas_internas())


def es_cita_valida(cita: str) -> tuple[bool, str]:
    """(válida, motivo). Una POL debe existir; las demás deben tener formato reconocido."""
    cita = (cita or "").strip()
    if not cita:
        return False, "cita vacía"
    if m := RE_POL.search(cita):
        codigo = f"POL-{m.group(1)}" + (f".{m.group(2)}" if m.group(2) else "")
        conocidas = reglas_internas() if m.group(2) else politicas_internas()
        if conocidas and codigo.upper() not in conocidas:
            return False, f"{codigo} no existe en LSA-2026"
        return True, ""
    if m := RE_API.search(cita):
        if 1 <= int(m.group(1)) <= 10:
            return True, ""
        return False, f"API{m.group(1)}:2023 no existe (son API1 a API10)"
    if m := RE_TOP10.search(cita):
        if 1 <= int(m.group(1)) <= 10:
            return True, ""
        return False, f"A{m.group(1)}:2025 no existe (son A01 a A10)"
    if m := RE_ASVS.search(cita):
        if 1 <= int(m.group(1)) <= 17:
            return True, ""
        return False, f"el capítulo V{m.group(1)} no existe en ASVS 5.0 (son V1 a V17)"
    if RE_NIST.search(cita) or RE_CHEAT.search(cita) or RE_REGULATORIO.search(cita):
        return True, ""
    return False, f"formato de cita no reconocido: {cita!r}"


def normalizar_cita(cita: str) -> str:
    """Forma canónica para comparar con la hoja de respuestas y con lo recuperado."""
    cita = " ".join((cita or "").split())
    if m := RE_POL.search(cita):
        return f"POL-{m.group(1)}" + (f".{m.group(2)}" if m.group(2) else "")
    if m := RE_API.search(cita):
        return f"API{int(m.group(1))}:2023"
    if m := RE_TOP10.search(cita):
        return f"A{m.group(1)}:2025"
    if m := RE_ASVS.search(cita):
        return f"ASVS V{m.group(1)}.{m.group(2)}.{m.group(3)}"
    if m := RE_NIST.search(cita):
        return f"NIST SP 800-{m.group(1).upper()}"
    return cita


def citas_en_texto(texto: str) -> list[str]:
    """Todas las citas reconocibles en un texto libre, normalizadas y sin repetir."""
    encontradas: list[str] = []
    for patron in (RE_POL, RE_API, RE_TOP10, RE_ASVS, RE_NIST):
        for m in patron.finditer(texto or ""):
            encontradas.append(normalizar_cita(m.group(0)))
    return list(dict.fromkeys(encontradas))


__all__ = ["citas_en_texto", "es_cita_valida", "normalizar_cita", "politicas_internas",
           "reglas_internas"]
