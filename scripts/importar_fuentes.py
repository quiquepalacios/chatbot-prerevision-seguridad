"""importar_fuentes.py — suma al RAG fuentes que ya tienes en una carpeta local.

Complementa a `descargar_fuentes.py`: sirve para documentos que no se bajan de
internet (una guía interna, un PDF que te pasaron, un .zip con un sitio) o
para trabajar sin red. Recorre la carpeta (y los .zip que contenga), decide qué
hacer con cada archivo y copia los útiles a `conocimiento/fuentes/propias/`,
desde donde `scripts/indexar.py` los procesa como cualquier otra fuente.

Para cada archivo, en este orden:
    1. excluido       una regla de `conocimiento/reglas_importacion.json` lo descarta
    2. no soportado   el formato no tiene cargador (ver app/rag/cargadores.EXTENSIONES)
    3. duplicado      ya hay un archivo idéntico (mismo SHA-256) en conocimiento/
    4. otro formato   el mismo documento ya está en un formato mejor (CSV > MD > HTML
                      > DOCX > TXT > PDF): p. ej. el PDF de ASVS cuando está el CSV
    5. sin texto      no se pudo extraer texto útil (PDF escaneado, archivo dañado)
    6. importado      se copia y se anota en manifiesto.json con su licencia

Uso (desde la raíz del repositorio):
    python -m scripts.importar_fuentes --desde "C:/ruta/a/mis_fuentes"
    python -m scripts.importar_fuentes --desde ../fuente_informacion/contenido --simular

Los archivos de la carpeta de origen se tratan como datos no confiables: solo
se leen, los .zip se abren sin extraer rutas (cada miembro se guarda con su
nombre de archivo, nunca con su carpeta) y con límite de tamaño.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator

from app import config
from app.rag import cargadores
from scripts.descargar_fuentes import Fuente, Resultado, escribir_manifiesto

GRUPO = "propias"
RUTA_REGLAS = config.CARPETA_CONOCIMIENTO / "reglas_importacion.json"
MAX_BYTES = 50 * 1024 * 1024          # por archivo (también por miembro de un zip)
MIN_PALABRAS = 50                     # menos que esto: no hay texto que indexar
SIN_LICENCIA = "sin indicar (revisar antes de publicar el repositorio)"


@dataclass
class Candidato:
    nombre: str                       # nombre con el que se guardaría
    origen: str                       # cómo se muestra en el reporte
    leer: Callable[[], bytes]


@dataclass
class Decision:
    origen: str
    estado: str                       # importado | excluido | no soportado | duplicado | ...
    detalle: str = ""
    destino: str = ""
    palabras: int = 0


# ---------------------------------------------------------------------------
# Reglas
# ---------------------------------------------------------------------------
def cargar_reglas(ruta: Path = RUTA_REGLAS) -> dict:
    if not ruta.exists():
        return {"excluir": [], "zip": {}, "licencias": []}
    return json.loads(ruta.read_text("utf-8"))


def _coincide(nombre: str, patron: str) -> bool:
    return fnmatch.fnmatch(nombre.lower(), patron.lower())


def motivo_exclusion(nombre: str, reglas: dict) -> str | None:
    for regla in reglas.get("excluir", []):
        if _coincide(nombre, regla["patron"]):
            return regla.get("motivo", f"coincide con {regla['patron']}")
    return None


def licencia_de(nombre: str, reglas: dict) -> str:
    for regla in reglas.get("licencias", []):
        if _coincide(nombre, regla["patron"]):
            return regla["licencia"]
    return SIN_LICENCIA


# ---------------------------------------------------------------------------
# Recorrido del origen (carpetas y .zip)
# ---------------------------------------------------------------------------
def _miembros_zip(ruta: Path, reglas: dict) -> Iterator[Candidato | Decision]:
    regla = reglas.get("zip", {}).get(ruta.name, {})
    incluir = regla.get("incluir") or ["*"]
    try:
        archivo = zipfile.ZipFile(ruta)
    except zipfile.BadZipFile:
        yield Decision(ruta.name, "error", "zip dañado")
        return
    with archivo:
        for info in archivo.infolist():
            if info.is_dir():
                continue
            interno = PurePosixPath(info.filename)
            if not any(_coincide(str(interno), p) for p in incluir):
                continue                       # fuera de la selección: ni se reporta
            origen = f"{ruta.name}:{interno}"
            if info.file_size > MAX_BYTES:
                yield Decision(origen, "error", f"supera {MAX_BYTES // 2**20} MB")
                continue
            yield Candidato(interno.name, origen,
                            lambda a=archivo, i=info: a.read(i))


def recorrer(origen: Path, reglas: dict) -> Iterator[Candidato | Decision]:
    for ruta in sorted(p for p in origen.rglob("*") if p.is_file()):
        if ruta.is_symlink() or ruta.name.startswith((".", "~$")):
            continue
        relativo = str(ruta.relative_to(origen))
        if motivo := motivo_exclusion(ruta.name, reglas):
            yield Decision(relativo, "excluido", motivo)
            continue
        if ruta.suffix.lower() == ".zip":
            yield from _miembros_zip(ruta, reglas)
            continue
        if ruta.stat().st_size > MAX_BYTES:
            yield Decision(relativo, "error", f"supera {MAX_BYTES // 2**20} MB")
            continue
        yield Candidato(ruta.name, relativo, ruta.read_bytes)


# ---------------------------------------------------------------------------
# Decisión por archivo
# ---------------------------------------------------------------------------
def _sha256(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def _existentes(conocimiento: Path) -> tuple[dict[str, Path], dict[str, Path]]:
    """Archivos ya presentes en conocimiento/: por SHA-256 y por documento."""
    por_hash: dict[str, Path] = {}
    por_clave: dict[str, Path] = {}
    for ruta in sorted(p for p in conocimiento.rglob("*")
                       if p.is_file() and p.suffix.lower() in cargadores.EXTENSIONES):
        por_hash.setdefault(_sha256(ruta.read_bytes()), ruta)
        clave = cargadores.clave_documento(ruta)
        actual = por_clave.get(clave)
        if actual is None or (cargadores.PRIORIDAD_FORMATO.get(ruta.suffix.lower(), 9)
                              < cargadores.PRIORIDAD_FORMATO.get(actual.suffix.lower(), 9)):
            por_clave[clave] = ruta
    return por_hash, por_clave


def importar(origen: Path, conocimiento: Path, reglas: dict,
             simular: bool = False) -> tuple[list[Decision], list[Resultado]]:
    destino = conocimiento / "fuentes" / GRUPO
    por_hash, por_clave = _existentes(conocimiento)
    decisiones: list[Decision] = []
    resultados: list[Resultado] = []

    def rel(p: Path) -> str:
        return p.relative_to(conocimiento).as_posix()

    for item in recorrer(origen, reglas):
        if isinstance(item, Decision):
            decisiones.append(item)
            continue
        nombre = item.nombre
        sufijo = Path(nombre).suffix.lower()
        if motivo := motivo_exclusion(nombre, reglas):           # miembros de un zip
            decisiones.append(Decision(item.origen, "excluido", motivo))
            continue
        if sufijo not in cargadores.EXTENSIONES:
            decisiones.append(Decision(item.origen, "no soportado",
                                       f"formato {sufijo or '(sin extensión)'}"))
            continue
        datos = item.leer()
        huella = _sha256(datos)
        if huella in por_hash:
            decisiones.append(Decision(item.origen, "duplicado",
                                       f"idéntico a {rel(por_hash[huella])}"))
            continue
        clave = cargadores.clave_documento(Path(nombre))
        previo = por_clave.get(clave)
        if previo is not None and (cargadores.PRIORIDAD_FORMATO.get(previo.suffix.lower(), 9)
                                   <= cargadores.PRIORIDAD_FORMATO.get(sufijo, 9)):
            decisiones.append(Decision(item.origen, "otro formato",
                                       f"ya está como {rel(previo)}"))
            continue

        # Se comprueba que el cargador saque texto antes de copiarlo.
        with tempfile.TemporaryDirectory() as tmp:
            prueba = Path(tmp) / nombre
            prueba.write_bytes(datos)
            try:
                palabras = cargadores.cargar(prueba).palabras
            except Exception as exc:                  # noqa: BLE001 - archivo dañado
                decisiones.append(Decision(item.origen, "error",
                                           f"no se pudo leer: {type(exc).__name__}"))
                continue
        if palabras < MIN_PALABRAS:
            decisiones.append(Decision(item.origen, "sin texto",
                                       f"{palabras} palabras (¿PDF escaneado?)"))
            continue

        ruta_final = destino / nombre
        if not simular:
            ruta_final.parent.mkdir(parents=True, exist_ok=True)
            temporal = ruta_final.with_suffix(ruta_final.suffix + ".parcial")
            temporal.write_bytes(datos)
            temporal.replace(ruta_final)
        por_hash[huella] = ruta_final
        por_clave[clave] = ruta_final
        decisiones.append(Decision(item.origen, "importado", destino=rel(ruta_final),
                                   palabras=palabras))
        fuente = Fuente(GRUPO, nombre, f"local:{item.origen}", licencia_de(nombre, reglas),
                        sufijo.lstrip("."))
        resultados.append(Resultado(fuente, "descargado", len(datos), huella))
    return decisiones, resultados


def imprimir_reporte(decisiones: list[Decision]) -> None:
    ancho = min(max((len(d.origen) for d in decisiones), default=10), 70)
    for d in decisiones:
        detalle = d.detalle or (f"-> {d.destino} ({d.palabras} palabras)" if d.destino else "")
        print(f"  {d.estado:<13} {d.origen[:ancho]:<{ancho}}  {detalle}")
    conteo: dict[str, int] = {}
    for d in decisiones:
        conteo[d.estado] = conteo.get(d.estado, 0) + 1
    print("\nResumen: " + ", ".join(f"{n} {e}" for e, n in sorted(conteo.items())))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Importa fuentes locales al conocimiento del RAG.")
    parser.add_argument("--desde", type=Path, required=True,
                        help="carpeta con los archivos (se recorren subcarpetas y .zip)")
    parser.add_argument("--reglas", type=Path, default=RUTA_REGLAS,
                        help="JSON con exclusiones, selección de .zip y licencias")
    parser.add_argument("--simular", action="store_true",
                        help="muestra qué haría sin copiar nada")
    args = parser.parse_args(argv)

    origen = args.desde.resolve()
    if not origen.is_dir():
        print(f"No existe la carpeta: {origen}")
        return 2
    conocimiento = config.CARPETA_CONOCIMIENTO
    if origen == conocimiento or conocimiento in origen.parents:
        print("La carpeta de origen no puede estar dentro de conocimiento/.")
        return 2

    reglas = cargar_reglas(args.reglas)
    print(f"Revisando {origen}{'  (simulación: no se copia nada)' if args.simular else ''}\n")
    decisiones, resultados = importar(origen, conocimiento, reglas, simular=args.simular)
    imprimir_reporte(decisiones)
    if resultados and not args.simular:
        escribir_manifiesto(resultados, conocimiento / "fuentes")
        sin_licencia = [r.fuente.nombre for r in resultados if r.fuente.licencia == SIN_LICENCIA]
        if sin_licencia:
            print(f"\nAVISO: {len(sin_licencia)} archivo(s) sin licencia conocida: "
                  f"{', '.join(sin_licencia)}. Agrega su licencia en {args.reglas.name}.")
        print("\nSiguiente paso:  python -m scripts.indexar")
    elif not resultados:
        print("\nNo hay nada nuevo que importar: todo ya está en conocimiento/ o fue excluido.")
    if any(d.estado == "error" for d in decisiones):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
