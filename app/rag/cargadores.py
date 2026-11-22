"""cargadores.py — de cualquier archivo de conocimiento a bloques de texto limpio.

Un cargador por formato (PDF, Markdown, HTML, texto, CSV, DOCX) y dos cargadores
especiales que reconocen la estructura de las fuentes principales:

- **Lineamientos LSA-2026** (Markdown con reglas `**POL-XX.N**`): un bloque por
  regla, con la política, el título y su "Revisión obligatoria".
- **OWASP ASVS** (CSV con `req_id` y `req_description`): un bloque por requisito.

Esos bloques son *atómicos*: el indexador no los vuelve a trocear, porque cada
uno ya es una unidad citable (`POL-01.1`, `ASVS V8.2.1`). El resto de bloques
(secciones de Markdown, páginas de PDF) se trocean después por oraciones.

Limpieza que se aplica (y que se cuenta en la diapositiva de datos):
- PDF: encabezados y pies que se repiten en las páginas, números de página,
  líneas de índice con puntos ("Introduction ....... 4"), palabras separadas
  por guion al final de línea, líneas cortadas que se unen en párrafos.
- Markdown/HTML: imágenes, enlaces (queda el texto), etiquetas HTML, filas
  separadoras de tablas y marcas de formato.
- Todos: espacios repetidos, caracteres invisibles y párrafos duplicados.

Agregar una fuente nueva no requiere tocar código: basta con dejar el archivo
en `conocimiento/fuentes/` (o importarlo con `scripts/importar_fuentes.py`).
Solo hace falta un cargador nuevo si el formato no está en `EXTENSIONES`.
"""
from __future__ import annotations

import csv
import hashlib
import re
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

EXTENSIONES = {".pdf", ".md", ".markdown", ".txt", ".html", ".htm", ".csv", ".docx"}


class FormatoNoSoportado(ValueError):
    pass


@dataclass
class Bloque:
    texto: str
    seccion: str = ""
    pagina: int | None = None
    cita: str = ""                 # ID citable propio (POL-01.1, ASVS V1.1.1)
    atomico: bool = False          # True = no se vuelve a trocear
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class Documento:
    ruta: Path
    fuente: str                    # cita base: "NIST SP 800-207", "API1:2023"
    titulo: str
    tipo: str                      # lineamiento | asvs | owasp | nist | otro
    formato: str                   # pdf | md | html | txt | csv | docx
    bloques: list[Bloque]
    limpieza: Counter = field(default_factory=Counter)   # qué se quitó y cuánto

    @property
    def palabras(self) -> int:
        return sum(len(b.texto.split()) for b in self.bloques)


# ---------------------------------------------------------------------------
# Texto común
# ---------------------------------------------------------------------------
_INVISIBLES = dict.fromkeys(map(ord, "­​‌‍﻿"), None)


def normalizar_espacios(texto: str) -> str:
    texto = unicodedata.normalize("NFKC", texto).translate(_INVISIBLES)
    texto = texto.replace(" ", " ")
    return re.sub(r"[ \t]+", " ", texto).strip()


def _sin_duplicados(bloques: list[Bloque], limpieza: Counter) -> list[Bloque]:
    vistos: set[str] = set()
    salida: list[Bloque] = []
    for b in bloques:
        clave = hashlib.sha1(" ".join(b.texto.lower().split()).encode()).hexdigest()
        if clave in vistos:
            limpieza["parrafos_duplicados"] += 1
            continue
        vistos.add(clave)
        salida.append(b)
    return salida


# ---------------------------------------------------------------------------
# Cita base y tipo, deducidos del nombre o del título
# ---------------------------------------------------------------------------
RE_NIST = re.compile(r"NIST\.SP\.(\d{3}-[0-9A-Za-z]+(?:-\d+)?)", re.IGNORECASE)
RE_API = re.compile(r"\bAPI(\d{1,2}):2023\b")
RE_TOP10 = re.compile(r"\bA(\d{2}):2025\b")


def cita_base(ruta: Path, titulo: str) -> tuple[str, str]:
    """Devuelve (cita, tipo) para un documento genérico."""
    nombre = ruta.stem
    if m := RE_NIST.search(nombre):
        return f"NIST SP {m.group(1).upper()}", "nist"
    if m := RE_API.search(titulo):
        return f"API{int(m.group(1))}:2023", "owasp"
    if m := RE_TOP10.search(titulo) or RE_TOP10.search(nombre.replace("_", ":", 1)):
        return f"A{m.group(1)}:2025", "owasp"
    if nombre.endswith("_Cheat_Sheet"):
        return f"OWASP Cheat Sheet: {nombre[:-len('_Cheat_Sheet')].replace('_', ' ')}", "owasp"
    return nombre.replace("_", " ").strip(), "otro"


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
RE_IMAGEN = re.compile(r"!\[[^\]]*\]\([^)]*\)(\{[^}]*\})?")
RE_ENLACE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
RE_TITULO_MD = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
RE_FILA_SEPARADORA = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
RE_ETIQUETA = re.compile(r"<[^>]+>")
RE_FORMATO = re.compile(r"(\*\*|__|`)")
# Secciones que no sirven para recomendar: cifras de incidencia, listas de CWE,
# bibliografía y agradecimientos. Solo meten ruido en la búsqueda.
RE_SECCION_OMITIDA = re.compile(
    r"^(tabla de puntuaci[oó]n|score table|factors|lista de cwe.*|list of mapped cwes|"
    r"cwes? mapeados?|referencias|references|related articles|acknowledg(e)?ments?|"
    r"agradecimientos|authors?|autores)\s*$", re.IGNORECASE)


def _limpiar_linea_md(linea: str, limpieza: Counter) -> str:
    if RE_IMAGEN.search(linea):
        limpieza["imagenes"] += len(RE_IMAGEN.findall(linea))
        linea = RE_IMAGEN.sub("", linea)
    linea = RE_ENLACE.sub(r"\1", linea)
    if RE_ETIQUETA.search(linea):
        limpieza["etiquetas_html"] += 1
        linea = RE_ETIQUETA.sub(" ", linea)
    linea = RE_FORMATO.sub("", linea)
    if linea.lstrip().startswith("|"):                       # fila de tabla
        celdas = [c.strip() for c in linea.strip().strip("|").split("|")]
        linea = " ; ".join(c for c in celdas if c)
    return normalizar_espacios(linea)


def secciones_markdown(texto: str, limpieza: Counter | None = None) -> tuple[str, list[Bloque]]:
    """Corta por títulos: cada sección es un bloque con su ruta de títulos."""
    limpieza = limpieza if limpieza is not None else Counter()
    texto = re.sub(r"\A---\n.*?\n---\n", "", texto, flags=re.DOTALL)   # front matter
    titulo_doc = ""
    pila: list[str] = []
    bloques: list[Bloque] = []
    actual: list[str] = []
    en_codigo = False

    def cerrar() -> None:
        cuerpo = " ".join(p for p in actual if p).strip()
        if cuerpo and pila and RE_SECCION_OMITIDA.match(pila[-1]):
            limpieza["secciones_omitidas"] += 1          # tablas de cifras, referencias
        elif cuerpo:
            bloques.append(Bloque(texto=cuerpo, seccion=" > ".join(pila)))
        actual.clear()

    for linea in texto.splitlines():
        if linea.strip().startswith("```"):
            en_codigo = not en_codigo
            continue
        if en_codigo:                         # el código se conserva, en una línea
            actual.append(normalizar_espacios(linea))
            continue
        if RE_FILA_SEPARADORA.match(linea) and "-" in linea:
            limpieza["filas_separadoras"] += 1
            continue
        if m := RE_TITULO_MD.match(linea):
            cerrar()
            nivel, nombre = len(m.group(1)), _limpiar_linea_md(m.group(2), limpieza)
            if nivel == 1 and not titulo_doc:
                titulo_doc = nombre
            pila[:] = pila[: max(nivel - 1, 0)] + [nombre]
            continue
        limpia = _limpiar_linea_md(linea, limpieza)
        if limpia:
            actual.append(limpia)
    cerrar()
    return titulo_doc, bloques


# ---------------------------------------------------------------------------
# Lineamientos internos LSA-2026 (un bloque por regla)
# ---------------------------------------------------------------------------
RE_POLITICA = re.compile(r"^##\s+(POL-\d{2})\s+(.+)$")
RE_REGLA = re.compile(r"^-\s+\*\*(POL-\d{2}\.\d+)\*\*\s+(.+)$")
RE_REVISION = re.compile(r"^\*\*Revisi[oó]n obligatoria:\*\*\s*(.+)$")
RE_REFERENCIAS = re.compile(r"^\*\*Referencias:\*\*\s*(.+)$")
RE_OBJETIVO = re.compile(r"^\*\*Objetivo:\*\*\s*(.+)$")


def es_lineamiento(texto: str) -> bool:
    return bool(re.search(r"^-\s+\*\*POL-\d{2}\.\d+\*\*", texto, re.MULTILINE))


def bloques_lineamientos(texto: str) -> list[Bloque]:
    """Cada regla es un bloque; la política aporta título, revisión y referencias."""
    politicas: list[dict[str, Any]] = []
    for linea in texto.splitlines():
        linea = linea.strip()
        if m := RE_POLITICA.match(linea):
            politicas.append({"codigo": m.group(1), "titulo": m.group(2).strip(),
                              "reglas": [], "revision": "", "referencias": "",
                              "objetivo": ""})
        elif not politicas:
            continue
        elif m := RE_OBJETIVO.match(linea):
            politicas[-1]["objetivo"] = m.group(1).strip()
        elif m := RE_REGLA.match(linea):
            politicas[-1]["reglas"].append((m.group(1), m.group(2)))
        elif m := RE_REVISION.match(linea):
            politicas[-1]["revision"] = m.group(1).strip()
        elif m := RE_REFERENCIAS.match(linea):
            politicas[-1]["referencias"] = m.group(1).strip()

    bloques: list[Bloque] = []
    for p in politicas:
        for codigo, regla in p["reglas"]:
            regla = normalizar_espacios(RE_FORMATO.sub("", regla))
            bloques.append(Bloque(
                texto=f"{codigo} ({p['codigo']} {p['titulo']}): {regla}",
                seccion=f"{p['codigo']} {p['titulo']}", cita=codigo, atomico=True,
                extra={"politica": p["codigo"], "titulo_politica": p["titulo"],
                       "revision_obligatoria": p["revision"],
                       "referencias": p["referencias"], "objetivo": p["objetivo"]},
            ))
    return bloques


# ---------------------------------------------------------------------------
# CSV (ASVS o genérico)
# ---------------------------------------------------------------------------
def _leer_csv(ruta: Path) -> list[dict[str, str]]:
    with ruta.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def bloques_csv(filas: list[dict[str, str]], nombre: str) -> tuple[str, list[Bloque]]:
    """ASVS: un requisito por bloque. Otro CSV: cada fila como 'columna: valor'."""
    if filas and {"req_id", "req_description"} <= set(filas[0]):
        bloques = []
        for f in filas:
            rid = (f.get("req_id") or "").strip()
            descripcion = normalizar_espacios(f.get("req_description") or "")
            if not rid or not descripcion:
                continue
            seccion = f"{f.get('chapter_id', '')} {f.get('chapter_name', '')} > " \
                      f"{f.get('section_id', '')} {f.get('section_name', '')}".strip(" >")
            bloques.append(Bloque(
                texto=f"ASVS {rid} ({seccion}): {descripcion}", seccion=seccion,
                cita=f"ASVS {rid}", atomico=True,
                extra={"nivel_asvs": (f.get("L") or "").strip(),
                       "capitulo": (f.get("chapter_id") or "").strip()},
            ))
        return "asvs", bloques
    bloques = []
    for i, f in enumerate(filas, 1):
        texto = "; ".join(f"{k}: {normalizar_espacios(v)}" for k, v in f.items()
                          if k and v and v.strip())
        if texto:
            bloques.append(Bloque(texto=texto, cita=f"{nombre} fila {i}", atomico=True))
    return "otro", bloques


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
RE_NUM_PAGINA = re.compile(r"^(p[aá]g(ina)?\.?|page)?\s*([ivxlcdm]+|\d+)(\s*(of|de)\s*\d+)?$",
                           re.IGNORECASE)
RE_INDICE = re.compile(r"(\.\s?){4,}\s*[ivxlcdm\d]+$", re.IGNORECASE)
RE_TITULO_PDF = re.compile(r"^(\d+(\.\d+){0,3}|Appendix [A-Z]\.?)\s+[A-Z][^.]{2,90}$")
ZONA = 4                                     # líneas de arriba y abajo que se miran


def _zona(lineas: list[str]) -> int:
    """En una página corta la zona se achica: el cuerpo no cuenta como encabezado."""
    return min(ZONA, len(lineas) // 3)


def _clave_repeticion(linea: str) -> str:
    return re.sub(r"\d+", "#", " ".join(linea.lower().split()))


def limpiar_paginas_pdf(paginas: list[str], limpieza: Counter) -> list[Bloque]:
    """Quita encabezados/pies/números/índice y une las líneas en párrafos."""
    lineas_por_pagina = [[normalizar_espacios(l) for l in p.splitlines()] for p in paginas]
    lineas_por_pagina = [[l for l in ls if l] for ls in lineas_por_pagina]

    # Lo que aparece en la cabecera o el pie de muchas páginas es encabezado/pie.
    conteo: Counter = Counter()
    for ls in lineas_por_pagina:
        z = _zona(ls)
        zona = set(_clave_repeticion(l) for l in (ls[:z] + ls[-z:] if z else []))
        conteo.update(zona)
    minimo = max(3, int(len(paginas) * 0.4))
    repetidas = {k for k, n in conteo.items() if n >= minimo} if len(paginas) >= 4 else set()

    bloques: list[Bloque] = []
    seccion = ""
    for numero, ls in enumerate(lineas_por_pagina, 1):
        parrafo: list[str] = []

        def cerrar() -> None:
            texto = re.sub(r"\s+", " ", " ".join(parrafo)).strip()
            if texto:
                bloques.append(Bloque(texto=texto, seccion=seccion, pagina=numero))
            parrafo.clear()

        z = _zona(ls)
        for i, linea in enumerate(ls):
            en_zona = i < z or i >= len(ls) - z
            if en_zona and _clave_repeticion(linea) in repetidas:
                limpieza["encabezados_pies"] += 1
                continue
            if RE_NUM_PAGINA.match(linea):
                limpieza["numeros_pagina"] += 1
                continue
            if RE_INDICE.search(linea):
                limpieza["lineas_indice"] += 1
                continue
            if RE_TITULO_PDF.match(linea) and len(linea.split()) <= 12:
                cerrar()
                seccion = linea
                continue
            if parrafo and parrafo[-1].endswith("-") and linea[:1].islower():
                parrafo[-1] = parrafo[-1] + linea          # "end-to-" + "end"
                limpieza["guiones_unidos"] += 1
            else:
                parrafo.append(linea)
        cerrar()
    return bloques


def _paginas_pdf(ruta: Path) -> list[str]:
    import logging

    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    return [p.extract_text() or "" for p in PdfReader(str(ruta)).pages]


# ---------------------------------------------------------------------------
# HTML y DOCX
# ---------------------------------------------------------------------------
def markdown_desde_html(html: str) -> str:
    """Convierte lo esencial del HTML a Markdown y reutiliza ese cargador."""
    from bs4 import BeautifulSoup

    sopa = BeautifulSoup(html, "html.parser")
    for basura in sopa(["script", "style", "nav", "header", "footer", "aside", "form"]):
        basura.decompose()
    raiz = sopa.find("article") or sopa.find("main") or sopa.body or sopa
    lineas: list[str] = []
    for nodo in raiz.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "td", "th"]):
        if nodo.find_parent(["li", "td", "th"]) and nodo.name not in ("td", "th"):
            continue                                       # evita contar dos veces
        texto = nodo.get_text(" ", strip=True)
        if not texto:
            continue
        if nodo.name.startswith("h"):
            lineas.append("#" * int(nodo.name[1]) + " " + texto)
        else:
            lineas.append(texto)
    return "\n".join(lineas)


def markdown_desde_docx(ruta: Path) -> str:
    """Lee los párrafos del DOCX (sin dependencias) y marca los títulos."""
    with zipfile.ZipFile(ruta) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    lineas: list[str] = []
    for parrafo in re.findall(r"<w:p[ >].*?</w:p>", xml, flags=re.DOTALL):
        texto = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", parrafo))
        if not texto.strip():
            continue
        estilo = re.search(r'<w:pStyle w:val="(?:Heading|Ttulo|Titulo)(\d)"', parrafo)
        lineas.append(("#" * int(estilo.group(1)) + " " if estilo else "") + texto)
    return "\n".join(lineas)


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------
def _documento_md(ruta: Path, texto: str, formato: str, limpieza: Counter) -> Documento:
    if es_lineamiento(texto):
        bloques = bloques_lineamientos(texto)
        titulo = next((l.lstrip("# ").strip() for l in texto.splitlines()
                       if l.startswith("# ")), ruta.stem)
        codigo = re.search(r"C[oó]digo:\s*([A-Z]+-\d{4})", texto)
        return Documento(ruta, codigo.group(1) if codigo else "Lineamientos internos",
                         titulo, "lineamiento", formato, bloques, limpieza)
    titulo, bloques = secciones_markdown(texto, limpieza)
    titulo = titulo or ruta.stem.replace("_", " ")
    fuente, tipo = cita_base(ruta, titulo)
    return Documento(ruta, fuente, titulo, tipo, formato,
                     _sin_duplicados(bloques, limpieza), limpieza)


def cargar(ruta: Path) -> Documento:
    """Lee un archivo de conocimiento según su extensión."""
    ruta = Path(ruta)
    sufijo = ruta.suffix.lower()
    limpieza: Counter = Counter()
    if sufijo not in EXTENSIONES:
        raise FormatoNoSoportado(f"{ruta.name}: formato {sufijo or '(sin extensión)'} "
                                 f"no soportado ({', '.join(sorted(EXTENSIONES))})")
    if sufijo in (".md", ".markdown"):
        return _documento_md(ruta, ruta.read_text("utf-8-sig"), "md", limpieza)
    if sufijo in (".html", ".htm"):
        return _documento_md(ruta, markdown_desde_html(ruta.read_text("utf-8", "replace")),
                             "html", limpieza)
    if sufijo == ".docx":
        return _documento_md(ruta, markdown_desde_docx(ruta), "docx", limpieza)
    if sufijo == ".txt":
        texto = ruta.read_text("utf-8-sig", "replace")
        if es_lineamiento(texto):
            return _documento_md(ruta, texto, "txt", limpieza)
        bloques = [Bloque(texto=normalizar_espacios(" ".join(p.split())))
                   for p in re.split(r"\n\s*\n", texto) if p.strip()]
        fuente, tipo = cita_base(ruta, ruta.stem)
        return Documento(ruta, fuente, ruta.stem.replace("_", " "), tipo, "txt",
                         _sin_duplicados(bloques, limpieza), limpieza)
    if sufijo == ".csv":
        tipo, bloques = bloques_csv(_leer_csv(ruta), ruta.stem)
        fuente = "OWASP ASVS 5.0" if tipo == "asvs" else ruta.stem.replace("_", " ")
        return Documento(ruta, fuente, ruta.stem.replace("_", " "), tipo, "csv",
                         bloques, limpieza)
    # PDF
    paginas = _paginas_pdf(ruta)
    bloques = _sin_duplicados(limpiar_paginas_pdf(paginas, limpieza), limpieza)
    titulo = next((l.strip() for l in (paginas[0] if paginas else "").splitlines()
                   if len(l.split()) >= 2), ruta.stem)
    fuente, tipo = cita_base(ruta, titulo)
    limpieza["paginas"] = len(paginas)
    return Documento(ruta, fuente, normalizar_espacios(titulo), tipo, "pdf", bloques, limpieza)


# Si el mismo documento llega en varios formatos se indexa uno solo: el que
# conserva mejor la estructura (el CSV de ASVS trae un requisito por fila; el
# PDF mezcla encabezados y saltos de página).
PRIORIDAD_FORMATO = {".csv": 0, ".md": 1, ".markdown": 1, ".html": 2, ".htm": 2,
                     ".docx": 3, ".txt": 4, ".pdf": 5}


def clave_documento(ruta: Path) -> str:
    """Nombre sin extensión ni mayúsculas: 'NIST.SP.800-63b-4.pdf' -> 'nist.sp.800-63b-4'."""
    return ruta.stem.lower()


def sin_formatos_repetidos(rutas: list[Path]) -> tuple[list[Path], dict[Path, Path]]:
    """Devuelve (elegidas, descartadas -> la que se queda en su lugar)."""
    mejor: dict[str, Path] = {}
    for ruta in rutas:
        clave = clave_documento(ruta)
        actual = mejor.get(clave)
        if actual is None or (PRIORIDAD_FORMATO.get(ruta.suffix.lower(), 9)
                              < PRIORIDAD_FORMATO.get(actual.suffix.lower(), 9)):
            mejor[clave] = ruta
    elegidas = [r for r in rutas if mejor[clave_documento(r)] == r]
    descartadas = {r: mejor[clave_documento(r)] for r in rutas if r not in elegidas}
    return elegidas, descartadas


def archivos_de(carpeta: Path) -> list[Path]:
    """Archivos indexables de una carpeta (con subcarpetas), uno por documento."""
    rutas = sorted(p for p in Path(carpeta).rglob("*")
                   if p.is_file() and p.suffix.lower() in EXTENSIONES
                   and not p.name.startswith((".", "~$")))
    return sin_formatos_repetidos(rutas)[0]


Cargador = Callable[[Path], Documento]

__all__ = ["Bloque", "Documento", "EXTENSIONES", "FormatoNoSoportado", "PRIORIDAD_FORMATO",
           "archivos_de", "bloques_csv", "bloques_lineamientos", "cargar", "cita_base",
           "clave_documento", "limpiar_paginas_pdf", "markdown_desde_html",
           "secciones_markdown", "sin_formatos_repetidos"]
