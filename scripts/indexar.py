"""indexar.py — construye la base vectorial Chroma con todo `conocimiento/`.

Se ejecuta una vez (y cada vez que cambian las fuentes). El servidor solo lee
el índice. El conocimiento queda en tres capas: originales (tal cual llegaron),
datos limpios (conocimiento/procesado/, un JSON por documento) y fragmentos
(Chroma). Pasos:

    1. originales    conocimiento/lineamientos + conocimiento/fuentes (uno por documento)
    2. limpieza      app/rag/cargadores limpia cada original y app/rag/procesado lo guarda
                     en conocimiento/procesado/ (se reutiliza si el original no cambió)
    3. fragmentos    app/rag/fragmentos, A PARTIR DEL JSON LIMPIO: ~150 palabras con 25 de solape;
                     reglas POL y requisitos ASVS enteros
    4. repetidos     fuera los fragmentos con el mismo texto
    5. embeddings    granite multilingüe local -> Chroma (coseno), colección temporal
                     que reemplaza a la vigente solo si todo salió bien
    6. control       preguntas de prueba: ¿trae la cita esperada?, ¿qué umbral separa
                     lo que está en tema de lo que no?
    7. reporte       chroma_db/reporte_indice.json y docs/reporte_indice.md (las
                     cifras de la diapositiva de datos)

Uso (desde la raíz del repositorio):
    python -m scripts.indexar
    python -m scripts.indexar --simular      # limpia y trocea, sin embeddings ni Chroma
    python -m scripts.indexar --reprocesar   # vuelve a limpiar todo aunque no haya cambiado
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app import config
from app.rag import cargadores, procesado
from app.rag.embeddings import Embedder
from app.rag.fragmentos import Fragmento, fragmentar, sin_repetidos
from app.rag.indice import IndiceLineamientos, guardar_reporte
from scripts.importar_fuentes import cargar_reglas, motivo_exclusion

RUTA_CONTROL = config.CARPETA_CONOCIMIENTO / "preguntas_control.json"
RUTA_REPORTE_MD = config.BASE_DIR / "docs" / "reporte_indice.md"
LICENCIA_LINEAMIENTOS = "Documento ficticio del proyecto (Banco Andesur)"

GRUPOS = {
    "lineamiento": "Lineamientos LSA-2026 (ficticio)",
    "asvs": "OWASP ASVS 5.0",
    "nist": "NIST SP 800-204, 207, 218, 63B",
}


def _grupo(doc: cargadores.Documento) -> str:
    if doc.tipo in GRUPOS:
        return GRUPOS[doc.tipo]
    if doc.fuente.endswith(":2023"):
        return "OWASP API Security Top 10 2023"
    if doc.fuente.endswith(":2025"):
        return "OWASP Top 10:2025 (es)"
    if doc.fuente.startswith("OWASP Cheat Sheet"):
        return "OWASP Cheat Sheets"
    return "Otras fuentes"


def licencias_del_manifiesto(conocimiento: Path) -> dict[str, str]:
    ruta = conocimiento / "fuentes" / "manifiesto.json"
    if not ruta.exists():
        return {}
    try:
        archivos = json.loads(ruta.read_text("utf-8"))["archivos"]
    except (ValueError, KeyError):
        return {}
    return {f"fuentes/{a['archivo']}": a.get("licencia", "") for a in archivos}


# ---------------------------------------------------------------------------
# 1-4: originales -> datos limpios (procesado/) -> fragmentos
# ---------------------------------------------------------------------------
def preparar(conocimiento: Path, reglas: dict, avisar=print, reprocesar: bool = False,
             carpeta_procesado: Path | None = None) -> tuple[list[Fragmento], dict]:
    """Capa 1 -> capa 2 -> fragmentos. Los fragmentos salen SIEMPRE del JSON limpio."""
    carpeta_procesado = Path(carpeta_procesado or conocimiento / "procesado")
    licencias = licencias_del_manifiesto(conocimiento)
    documentos: list[dict[str, Any]] = []
    errores: list[dict[str, str]] = []
    excluidos: list[dict[str, str]] = []
    limpieza: Counter = Counter()
    todos: list[Fragmento] = []
    capas = Counter()
    vigentes: set[Path] = set()

    rutas = [r for r in cargadores.archivos_de(conocimiento)
             if carpeta_procesado not in r.parents]
    _, otros_formatos = cargadores.sin_formatos_repetidos(
        sorted(p for p in conocimiento.rglob("*")
               if p.is_file() and p.suffix.lower() in cargadores.EXTENSIONES
               and carpeta_procesado not in p.parents))
    for ruta in rutas:
        relativo = ruta.relative_to(conocimiento).as_posix()
        if motivo := motivo_exclusion(ruta.name, reglas):
            excluidos.append({"archivo": relativo, "motivo": motivo})
            avisar(f"  - {relativo}: excluido ({motivo})")
            continue
        destino = procesado.ruta_para(carpeta_procesado, relativo)
        huella = procesado.sha256(ruta)
        if not reprocesar and procesado.vigente(destino, huella):
            capas["reutilizados"] += 1
            accion = "limpio reutilizado"
        else:
            try:
                original = cargadores.cargar(ruta)
            except Exception as exc:                   # noqa: BLE001 - archivo dañado
                errores.append({"archivo": relativo, "error": f"{type(exc).__name__}: {exc}"})
                avisar(f"  ! {relativo}: {type(exc).__name__}: {exc}")
                continue
            licencia_original = (LICENCIA_LINEAMIENTOS if original.tipo == "lineamiento"
                                 else licencias.get(relativo, "sin indicar"))
            procesado.guardar(original, destino, relativo, huella, licencia_original)
            capas["procesados"] += 1
            accion = "limpiado"
        vigentes.add(destino)
        doc, meta = procesado.leer(destino)            # el índice sale de la capa limpia
        licencia = meta.get("licencia", "sin indicar")
        fragmentos = fragmentar(doc, relativo, licencia)
        limpieza.update(doc.limpieza)
        todos.extend(fragmentos)
        documentos.append({"archivo": relativo, "fuente": doc.fuente, "grupo": _grupo(doc),
                           "formato": doc.formato, "bloques": len(doc.bloques),
                           "palabras": doc.palabras, "fragmentos": len(fragmentos),
                           "procesado": destino.relative_to(conocimiento).as_posix()})
        avisar(f"  + {relativo}: {accion}, {len(fragmentos)} fragmentos")

    for viejo in procesado.huerfanos(carpeta_procesado, vigentes):
        viejo.unlink()
        capas["eliminados"] += 1
        avisar(f"  - {viejo.relative_to(conocimiento).as_posix()}: ya no tiene original, se borra")

    unicos, repetidos = sin_repetidos(todos)
    limpieza["fragmentos_repetidos"] += repetidos
    datos = {"documentos": documentos, "errores": errores, "excluidos": excluidos,
             "otro_formato": [{"archivo": p.relative_to(conocimiento).as_posix(),
                               "se_usa": q.relative_to(conocimiento).as_posix()}
                              for p, q in otros_formatos.items()],
             "limpieza": dict(sorted(limpieza.items())),
             "capas": {"originales": len(documentos), "limpios": len(vigentes),
                       "procesados_ahora": capas["procesados"],
                       "reutilizados": capas["reutilizados"],
                       "eliminados": capas["eliminados"],
                       "carpeta_limpios": carpeta_procesado.relative_to(conocimiento).as_posix()
                       if conocimiento in carpeta_procesado.parents else str(carpeta_procesado)}}
    return unicos, datos


def estadisticas(fragmentos: list[Fragmento], documentos: list[dict]) -> dict[str, Any]:
    def resumen(fs: list[Fragmento]) -> dict[str, Any]:
        largos = [f.palabras for f in fs] or [0]
        return {"fragmentos": len(fs), "palabras_min": min(largos),
                "palabras_mediana": round(statistics.median(largos)),
                "palabras_max": max(largos)}

    por_grupo: dict[str, list[Fragmento]] = defaultdict(list)
    grupo_de_archivo = {d["archivo"]: d["grupo"] for d in documentos}
    for f in fragmentos:
        por_grupo[grupo_de_archivo.get(f.metadatos["archivo"], "Otras fuentes")].append(f)
    grupos = {}
    for nombre in sorted(por_grupo):
        docs = [d for d in documentos if d["grupo"] == nombre]
        grupos[nombre] = {"documentos": len(docs), **resumen(por_grupo[nombre])}
    normales = [f for f in fragmentos if not f.metadatos.get("atomico")]
    return {"total": {"documentos": len(documentos), **resumen(fragmentos)},
            "troceados": resumen(normales),
            "atomicos": len(fragmentos) - len(normales),
            "por_grupo": grupos}


# ---------------------------------------------------------------------------
# 6: preguntas de control
# ---------------------------------------------------------------------------
def control(indice: IndiceLineamientos, ruta: Path = RUTA_CONTROL) -> dict[str, Any]:
    """Acierto@k de las preguntas en tema y el umbral que separa lo que no lo es."""
    if not ruta.exists():
        return {}
    datos = json.loads(ruta.read_text("utf-8"))
    en_tema = []
    for item in datos.get("en_tema", []):
        resultados = indice.buscar(item["pregunta"], umbral=-1.0)
        citas = [r.cita for r in resultados]
        acierto = any(c.startswith(tuple(item["esperadas"])) for c in citas)
        en_tema.append({"pregunta": item["pregunta"], "acierto": acierto, "citas": citas,
                        "mejor": round(resultados[0].score, 4) if resultados else 0.0})
    fuera = []
    for pregunta in datos.get("fuera_de_tema", []):
        resultados = indice.buscar(pregunta, k=1, umbral=-1.0)
        fuera.append({"pregunta": pregunta,
                      "mejor": round(resultados[0].score, 4) if resultados else 0.0,
                      "cita": resultados[0].cita if resultados else ""})
    min_tema = min((p["mejor"] for p in en_tema), default=0.0)
    max_fuera = max((p["mejor"] for p in fuera), default=0.0)
    return {
        "acierto": f"{sum(p['acierto'] for p in en_tema)}/{len(en_tema)}",
        "k": config.RAG_K_FINAL,
        "mejor_en_tema_min": min_tema,
        "mejor_fuera_de_tema_max": max_fuera,
        "umbral_sugerido": round((min_tema + max_fuera) / 2, 3) if min_tema > max_fuera else None,
        "umbral_actual": config.RAG_UMBRAL,
        "en_tema": en_tema,
        "fuera_de_tema": fuera,
    }


# ---------------------------------------------------------------------------
# 7: reporte
# ---------------------------------------------------------------------------
NOMBRES_LIMPIEZA = {
    "encabezados_pies": "Encabezados y pies de página repetidos",
    "numeros_pagina": "Números de página",
    "lineas_indice": "Líneas de índice (tabla de contenido)",
    "guiones_unidos": "Palabras cortadas con guion, unidas",
    "imagenes": "Imágenes de Markdown",
    "etiquetas_html": "Líneas con etiquetas HTML",
    "filas_separadoras": "Filas separadoras de tablas",
    "secciones_omitidas": "Secciones sin valor (cifras, CWE, referencias)",
    "parrafos_duplicados": "Párrafos duplicados dentro de un documento",
    "fragmentos_repetidos": "Fragmentos repetidos entre documentos",
    "paginas": "Páginas de PDF procesadas",
}


def reporte_markdown(reporte: dict[str, Any]) -> str:
    est = reporte["estadisticas"]
    t = est["total"]
    lineas = [
        "# Reporte del índice de lineamientos",
        "",
        f"Generado por `python -m scripts.indexar` el {reporte['fecha']}. "
        f"Embeddings: `{reporte['embeddings']}` (dimensión {reporte.get('dimension', '?')}). "
        f"Fragmentos de hasta {reporte['chunk'][0]} palabras con {reporte['chunk'][1]} de "
        "solape; las reglas POL y los requisitos ASVS se indexan enteros (uno por fragmento).",
        "",
        f"**Total: {t['documentos']} documentos y {t['fragmentos']} fragmentos** "
        f"(palabras por fragmento: mínimo {t['palabras_min']}, mediana "
        f"{t['palabras_mediana']}, máximo {t['palabras_max']}).",
        "",
        "| Fuente | Documentos | Fragmentos | Palabras (mín / mediana / máx) |",
        "|---|---:|---:|---|",
    ]
    for nombre, g in est["por_grupo"].items():
        lineas.append(f"| {nombre} | {g['documentos']} | {g['fragmentos']} | "
                      f"{g['palabras_min']} / {g['palabras_mediana']} / {g['palabras_max']} |")
    capas = reporte.get("capas")
    if capas:
        lineas += ["", "## Tres capas de datos", "",
                   "| Capa | Dónde | Qué guarda |", "|---|---|---|",
                   f"| 1. Originales | `conocimiento/lineamientos/` y `conocimiento/fuentes/` | "
                   f"{capas['originales']} archivos tal cual llegaron (PDF, CSV, Markdown), con su SHA-256 |",
                   f"| 2. Datos limpios | `conocimiento/{capas['carpeta_limpios']}/` | "
                   f"{capas['limpios']} JSON, uno por documento: texto limpio en bloques, metadatos y qué se quitó |",
                   f"| 3. Fragmentos | `chroma_db/` (se construye con `--indexar`) | "
                   f"{reporte['estadisticas']['total']['fragmentos']} fragmentos con sus embeddings |",
                   "", "El índice se construye a partir de la capa 2, no del original: lo que está en "
                   "`procesado/` es exactamente lo que recibe el RAG."]
    lineas += ["", "## Limpieza", "", "| Qué se quitó o corrigió | Cantidad |", "|---|---:|"]
    for clave, n in reporte["limpieza"].items():
        lineas.append(f"| {NOMBRES_LIMPIEZA.get(clave, clave)} | {n} |")
    if reporte.get("otro_formato") or reporte.get("excluidos"):
        lineas += ["", "## Archivos no indexados", ""]
        for o in reporte.get("otro_formato", []):
            lineas.append(f"- `{o['archivo']}`: mismo documento que `{o['se_usa']}`.")
        for e in reporte.get("excluidos", []):
            lineas.append(f"- `{e['archivo']}`: {e['motivo']}.")
    c = reporte.get("control") or {}
    if c:
        lineas += ["", "## Preguntas de control", "",
                   f"Acierto: **{c['acierto']}** (la cita esperada aparece entre los "
                   f"{c['k']} fragmentos que llegan al prompt). Mejor similitud en tema, "
                   f"mínima: {c['mejor_en_tema_min']}; fuera de tema, máxima: "
                   f"{c['mejor_fuera_de_tema_max']}. Umbral configurado: {c['umbral_actual']}"
                   + (f"; sugerido: {c['umbral_sugerido']}." if c.get("umbral_sugerido")
                      else "; las dos escalas se cruzan: el umbral solo no separa los temas "
                           "(lo fuera de alcance lo filtran las guardias y la FAQ)."),
                   "", "| Pregunta | ¿Acierta? | Citas recuperadas | Similitud |",
                   "|---|---|---|---:|"]
        for p in c["en_tema"]:
            lineas.append(f"| {p['pregunta']} | {'sí' if p['acierto'] else '**no**'} | "
                          f"{', '.join(p['citas'])} | {p['mejor']} |")
        for p in c["fuera_de_tema"]:
            lineas.append(f"| *(fuera de tema)* {p['pregunta']} | — | {p['cita']} | {p['mejor']} |")
    lineas.append("")
    return "\n".join(lineas)


def _imprimir_control(c: dict[str, Any]) -> None:
    if not c:
        return
    print(f"\nPreguntas de control: acierto {c['acierto']} | similitud en tema mín. "
          f"{c['mejor_en_tema_min']} | fuera de tema máx. {c['mejor_fuera_de_tema_max']} | "
          f"umbral {c['umbral_actual']}")
    for p in c["en_tema"]:
        if not p["acierto"]:
            print(f"  falla: {p['pregunta']} -> {', '.join(p['citas'])}")


def solo_control(embedder: Embedder | None = None) -> int:
    """Recalibrar sin reindexar: útil al cambiar el umbral o las preguntas."""
    indice = IndiceLineamientos(embedder=embedder)
    if not indice.disponible():
        print("No hay índice todavía: ejecuta 'python -m scripts.indexar'.")
        return 1
    ruta = Path(config.CHROMA_DIR) / "reporte_indice.json"
    reporte = json.loads(ruta.read_text("utf-8")) if ruta.exists() else None
    c = control(indice)
    _imprimir_control(c)
    if reporte:
        reporte["control"] = c
        guardar_reporte(reporte)
        RUTA_REPORTE_MD.write_text(reporte_markdown(reporte), "utf-8")
        print(f"Reporte actualizado: docs/{RUTA_REPORTE_MD.name}")
    return 0


def main(argv: list[str] | None = None, embedder: Embedder | None = None) -> int:
    parser = argparse.ArgumentParser(description="Construye el índice Chroma de lineamientos.")
    parser.add_argument("--reprocesar", action="store_true",
                        help="vuelve a generar la capa limpia aunque los originales no cambiaron")
    parser.add_argument("--simular", action="store_true",
                        help="carga y trocea sin calcular embeddings ni tocar Chroma")
    parser.add_argument("--sin-control", action="store_true",
                        help="omite las preguntas de control")
    parser.add_argument("--solo-control", action="store_true",
                        help="no reindexa: repite las preguntas de control sobre el índice actual")
    args = parser.parse_args(argv)

    if args.solo_control:
        return solo_control(embedder)

    conocimiento = config.CARPETA_CONOCIMIENTO
    inicio = time.perf_counter()
    print(f"Leyendo {conocimiento}")
    fragmentos, datos = preparar(conocimiento, cargar_reglas(), reprocesar=args.reprocesar)
    c = datos["capas"]
    print(f"\nCapas: {c['originales']} originales -> {c['limpios']} limpios en conocimiento/{c['carpeta_limpios']}/ "
          f"({c['procesados_ahora']} limpiados ahora, {c['reutilizados']} reutilizados, {c['eliminados']} borrados)")
    if not fragmentos:
        print("\nNo hay fragmentos que indexar. ¿Ejecutaste 'python -m scripts.descargar_fuentes'?")
        return 1
    est = estadisticas(fragmentos, datos["documentos"])
    print(f"\n{est['total']['documentos']} documentos -> {est['total']['fragmentos']} fragmentos "
          f"({est['atomicos']} enteros: reglas POL y requisitos ASVS)")
    for nombre, g in est["por_grupo"].items():
        print(f"  {nombre:<36} {g['documentos']:>3} docs {g['fragmentos']:>5} fragmentos "
              f"(mediana {g['palabras_mediana']} palabras)")
    if args.simular:
        print("\nSimulación: no se calcularon embeddings ni se tocó Chroma.")
        return 0

    print(f"\nCalculando embeddings con {config.MODELO_EMBEDDINGS} (la primera vez descarga "
          "el modelo)...")
    indice = IndiceLineamientos(embedder=embedder)
    t0 = time.perf_counter()
    indice.construir(fragmentos, {"documentos": est["total"]["documentos"],
                                  "fragmentos": est["total"]["fragmentos"]})
    segundos_embeddings = round(time.perf_counter() - t0, 1)

    reporte: dict[str, Any] = {
        "fecha": time.strftime("%Y-%m-%d %H:%M"),
        "embeddings": config.MODELO_EMBEDDINGS,
        "chunk": [config.CHUNK_MAX_PALABRAS, config.CHUNK_OVERLAP_PALABRAS],
        "estadisticas": est, **datos,
        "segundos": {"embeddings": segundos_embeddings,
                     "total": round(time.perf_counter() - inicio, 1)},
    }
    if embedder is None:
        from app.rag.embeddings import dimension

        reporte["dimension"] = dimension()
    if not args.sin_control:
        reporte["control"] = control(indice)
        _imprimir_control(reporte["control"])

    ruta_json = guardar_reporte(reporte)
    RUTA_REPORTE_MD.parent.mkdir(parents=True, exist_ok=True)
    RUTA_REPORTE_MD.write_text(reporte_markdown(reporte), "utf-8")
    print(f"\nÍndice listo en {config.CHROMA_DIR} ({segundos_embeddings} s de embeddings).")
    print(f"Reportes: {ruta_json.name} y docs/{RUTA_REPORTE_MD.name}")
    return 1 if datos["errores"] else 0


if __name__ == "__main__":
    sys.exit(main())
