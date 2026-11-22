"""descargar_fuentes.py — baja las fuentes públicas que alimentan el RAG.

Solo los lineamientos internos (LSA-2026, ficticios) viven en el repositorio.
Las guías públicas se descargan de sus sitios oficiales y quedan en
`conocimiento/fuentes/`, fuera de Git:

    asvs/              OWASP ASVS 5.0.0 en CSV (un requisito por fila, con su ID)
    api_top10_2023/    OWASP API Security Top 10 2023 (API1 … API10), Markdown
    top10_2025_es/     OWASP Top 10:2025 en español (A01 … A10), Markdown
    cheatsheets/       10 OWASP Cheat Sheets, Markdown
    nist/              NIST SP 800-204, 800-207, 800-218 y 800-63B-4, PDF

Las URLs de GitHub apuntan a un commit fijo: si OWASP cambia un archivo, el
índice no cambia sin que nadie se entere. Cada descarga se valida (un PDF
empieza por `%PDF`, el CSV trae su cabecera) y se anota en `manifiesto.json`
con su tamaño y su SHA-256, que es lo que el README cita como procedencia.

Uso (desde la raíz del repositorio):
    python -m scripts.descargar_fuentes            # baja lo que falta
    python -m scripts.descargar_fuentes --forzar   # vuelve a bajar todo
    python -m scripts.descargar_fuentes --solo nist

Licencias: OWASP publica bajo CC BY-SA 4.0 (se da atribución en el README);
las publicaciones de NIST son de dominio público.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

import httpx

from app import config

# Commits fijados el 21/11/2026 (rama master de cada repositorio).
COMMIT_API = "33cea37b2ffde3e2e62f6b5d79a029a22a846633"
COMMIT_TOP10 = "3a31f35346c3f4e90f350395124382fa53af2afe"
COMMIT_CHEATSHEETS = "c2a1e3e32cec12f27be0a3b1e771ce51288a363e"

RAW = "https://raw.githubusercontent.com/OWASP"
URL_API = f"{RAW}/API-Security/{COMMIT_API}/editions/2023/en"
URL_TOP10 = f"{RAW}/Top10/{COMMIT_TOP10}/2025/docs/es"
URL_CHEATSHEETS = f"{RAW}/CheatSheetSeries/{COMMIT_CHEATSHEETS}/cheatsheets"
URL_ASVS = ("https://github.com/OWASP/ASVS/releases/download/v5.0.0_release/"
            "OWASP_Application_Security_Verification_Standard_5.0.0_en.csv")
URL_NIST = "https://nvlpubs.nist.gov/nistpubs/SpecialPublications"

CC_BY_SA = "CC BY-SA 4.0 (OWASP Foundation)"
DOMINIO_PUBLICO = "Dominio público (NIST, Gobierno de EE. UU.)"


@dataclass(frozen=True)
class Fuente:
    grupo: str          # asvs | api_top10_2023 | top10_2025_es | cheatsheets | nist
    nombre: str         # nombre del archivo local
    url: str
    licencia: str
    tipo: str           # csv | md | pdf: decide la validación

    @property
    def ruta_relativa(self) -> str:
        return f"{self.grupo}/{self.nombre}"


def _api_top10() -> list[Fuente]:
    archivos = [
        "0xa1-broken-object-level-authorization.md",
        "0xa2-broken-authentication.md",
        "0xa3-broken-object-property-level-authorization.md",
        "0xa4-unrestricted-resource-consumption.md",
        "0xa5-broken-function-level-authorization.md",
        "0xa6-unrestricted-access-to-sensitive-business-flows.md",
        "0xa7-server-side-request-forgery.md",
        "0xa8-security-misconfiguration.md",
        "0xa9-improper-inventory-management.md",
        "0xaa-unsafe-consumption-of-apis.md",
    ]
    return [Fuente("api_top10_2023", a, f"{URL_API}/{a}", CC_BY_SA, "md") for a in archivos]


def _top10_2025() -> list[Fuente]:
    archivos = [
        "A01_2025-Broken_Access_Control.md",
        "A02_2025-Security_Misconfiguration.md",
        "A03_2025-Software_Supply_Chain_Failures.md",
        "A04_2025-Cryptographic_Failures.md",
        "A05_2025-Injection.md",
        "A06_2025-Insecure_Design.md",
        "A07_2025-Authentication_Failures.md",
        "A08_2025-Software_or_Data_Integrity_Failures.md",
        "A09_2025-Security_Logging_and_Alerting_Failures.md",
        "A10_2025-Mishandling_of_Exceptional_Conditions.md",
    ]
    return [Fuente("top10_2025_es", a, f"{URL_TOP10}/{a}", CC_BY_SA, "md") for a in archivos]


# Las 10 guías más cercanas a los riesgos que revisa el bot (autorización, APIs, tokens,
# microservicios, SSRF, archivos, registro, secretos, validación y asignación masiva).
CHEATSHEETS = [
    "Authorization",
    "REST_Security",
    "JSON_Web_Token",
    "Microservices_Security",
    "Server_Side_Request_Forgery_Prevention",
    "File_Upload",
    "Logging",
    "Secrets_Management",
    "Input_Validation",
    "Mass_Assignment",
]


def _cheatsheets() -> list[Fuente]:
    return [Fuente("cheatsheets", f"{n}_Cheat_Sheet.md",
                   f"{URL_CHEATSHEETS}/{n}_Cheat_Sheet.md", CC_BY_SA, "md")
            for n in CHEATSHEETS]


def _nist() -> list[Fuente]:
    archivos = ["NIST.SP.800-204.pdf", "NIST.SP.800-207.pdf", "NIST.SP.800-218.pdf",
                "NIST.SP.800-63b-4.pdf"]
    return [Fuente("nist", a, f"{URL_NIST}/{a}", DOMINIO_PUBLICO, "pdf") for a in archivos]


def catalogo() -> list[Fuente]:
    """Todas las fuentes públicas, en el orden en que se descargan."""
    # Nombre oficial: así un PDF o DOCX de ASVS con el mismo nombre se reconoce
    # como el mismo documento en otro formato y no se indexa dos veces.
    return ([Fuente("asvs", URL_ASVS.rsplit("/", 1)[1], URL_ASVS, CC_BY_SA, "csv")]
            + _api_top10() + _top10_2025() + _cheatsheets() + _nist())


GRUPOS = ("asvs", "api_top10_2023", "top10_2025_es", "cheatsheets", "nist")


# ---------------------------------------------------------------------------
# Validación: lo descargado es dato no confiable, se comprueba antes de usarlo
# ---------------------------------------------------------------------------
class ContenidoInvalido(ValueError):
    pass


def validar(fuente: Fuente, contenido: bytes) -> None:
    if len(contenido) < 200:
        raise ContenidoInvalido(f"archivo demasiado pequeño ({len(contenido)} bytes)")
    if fuente.tipo == "pdf":
        if not contenido.startswith(b"%PDF"):
            raise ContenidoInvalido("no es un PDF")
        return
    try:
        texto = contenido.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ContenidoInvalido("no es texto UTF-8") from exc
    if texto.lstrip().lower().startswith(("<!doctype html", "<html")):
        raise ContenidoInvalido("se recibió una página HTML en vez del archivo")
    if fuente.tipo == "csv" and not texto.startswith("chapter_id,"):
        raise ContenidoInvalido("el CSV no trae la cabecera esperada de ASVS")


# ---------------------------------------------------------------------------
# Descarga
# ---------------------------------------------------------------------------
@dataclass
class Resultado:
    fuente: Fuente
    estado: str         # descargado | existente | error
    bytes: int = 0
    sha256: str = ""
    detalle: str = ""


def _sha256(contenido: bytes) -> str:
    return hashlib.sha256(contenido).hexdigest()


def _bajar(cliente: httpx.Client, url: str, intentos: int = 3) -> bytes:
    ultimo: Exception | None = None
    for intento in range(1, intentos + 1):
        try:
            respuesta = cliente.get(url)
            respuesta.raise_for_status()
            return respuesta.content
        except (httpx.HTTPError,) as exc:
            ultimo = exc
            # un 404 no se arregla reintentando
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                break
            if intento < intentos:
                time.sleep(2 * intento)
    raise ultimo or RuntimeError("descarga fallida")


def descargar(fuentes: Iterable[Fuente], destino: Path, cliente: httpx.Client,
              forzar: bool = False,
              avisar: Callable[[str], None] = print) -> list[Resultado]:
    """Descarga cada fuente que falte; nunca deja un archivo a medias."""
    resultados: list[Resultado] = []
    for fuente in fuentes:
        ruta = destino / fuente.grupo / fuente.nombre
        if ruta.exists() and not forzar:
            contenido = ruta.read_bytes()
            resultados.append(Resultado(fuente, "existente", len(contenido), _sha256(contenido)))
            avisar(f"  = {fuente.ruta_relativa} (ya estaba)")
            continue
        try:
            contenido = _bajar(cliente, fuente.url)
            validar(fuente, contenido)
        except (httpx.HTTPError, ContenidoInvalido, RuntimeError) as exc:
            detalle = f"{type(exc).__name__}: {exc}"
            resultados.append(Resultado(fuente, "error", detalle=detalle))
            avisar(f"  ! {fuente.ruta_relativa}: {detalle}")
            continue
        ruta.parent.mkdir(parents=True, exist_ok=True)
        temporal = ruta.with_suffix(ruta.suffix + ".parcial")
        temporal.write_bytes(contenido)
        temporal.replace(ruta)
        resultados.append(Resultado(fuente, "descargado", len(contenido), _sha256(contenido)))
        avisar(f"  + {fuente.ruta_relativa} ({len(contenido) / 1024:.0f} KB)")
    return resultados


def escribir_manifiesto(resultados: list[Resultado], destino: Path) -> Path:
    """Procedencia de cada archivo: URL, licencia, tamaño y SHA-256."""
    ruta = destino / "manifiesto.json"
    previo: dict[str, dict] = {}
    if ruta.exists():
        try:
            previo = {a["archivo"]: a for a in json.loads(ruta.read_text("utf-8"))["archivos"]}
        except (ValueError, KeyError):
            previo = {}
    ahora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for r in resultados:
        if r.estado == "error":
            continue
        anterior = previo.get(r.fuente.ruta_relativa, {})
        previo[r.fuente.ruta_relativa] = {
            "archivo": r.fuente.ruta_relativa,
            "url": r.fuente.url,
            "licencia": r.fuente.licencia,
            "bytes": r.bytes,
            "sha256": r.sha256,
            "descargado": (ahora if r.estado == "descargado"
                           else anterior.get("descargado", ahora)),
        }
    datos = {"generado": ahora, "archivos": sorted(previo.values(), key=lambda a: a["archivo"])}
    ruta.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ruta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Descarga las fuentes públicas del RAG.")
    parser.add_argument("--forzar", action="store_true",
                        help="vuelve a descargar aunque el archivo ya exista")
    parser.add_argument("--solo", choices=GRUPOS, action="append",
                        help="descarga solo ese grupo (se puede repetir)")
    args = parser.parse_args(argv)

    fuentes = [f for f in catalogo() if not args.solo or f.grupo in args.solo]
    destino = config.CARPETA_FUENTES
    destino.mkdir(parents=True, exist_ok=True)
    print(f"Descargando {len(fuentes)} archivos en {destino}")

    cabeceras = {"User-Agent": "andesur-prerevision/1.0 (proyecto academico)"}
    with httpx.Client(timeout=60, follow_redirects=True, headers=cabeceras) as cliente:
        resultados = descargar(fuentes, destino, cliente, forzar=args.forzar)
    manifiesto = escribir_manifiesto(resultados, destino)

    errores = [r for r in resultados if r.estado == "error"]
    nuevos = sum(r.estado == "descargado" for r in resultados)
    print(f"\nListo: {nuevos} descargados, "
          f"{sum(r.estado == 'existente' for r in resultados)} ya estaban, "
          f"{len(errores)} con error. Manifiesto: {manifiesto.name}")
    if errores:
        print("Revisa la conexión (o el proxy) y vuelve a ejecutar: solo se bajan los que faltan.")
        return 1
    print("Siguiente paso:  python -m scripts.indexar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
