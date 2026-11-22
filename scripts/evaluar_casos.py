"""evaluar_casos.py — mide al bot contra la hoja de respuestas (diapositiva 11).

    1. escenarios   casos_prueba/escenarios.json: error, fuera de alcance, FAQ, filtros y
                    trampas. Los que no necesitan modelo se evalúan siempre.
    2. casos        caso_01..03 (solicitudes a alto nivel, como llegan en la realidad)
                    contra respuestas_esperadas.md (37 riesgos: 21 básicos, 16 avanzados).
                    Necesita el modelo configurado en el .env.
    3. métricas     detección de básicos (/21) y avanzados (/16), hallazgos no esperados,
                    citas correctas (regla POL exacta y política), marca "va a Seguridad",
                    latencia p50/p95 y tokens por caso.

Emparejamiento automático: cada hallazgo del bot se compara con los esperados del
caso por similitud semántica (granite, el mismo modelo del RAG) más un bono si cita
una regla POL esperada; se asigna 1 a 1 de mayor a menor puntaje sobre un umbral.
Es una aproximación: `emparejamientos.csv` deja cada par para revisarlo a mano, y
los "no esperados" pueden ser hallazgos válidos que la hoja no incluye.

Uso (desde la raíz del repositorio):
    python -m scripts.evaluar_casos                  # escenarios + 3 casos
    python -m scripts.evaluar_casos --casos 02       # un caso
    python -m scripts.evaluar_casos --sin-casos      # solo escenarios (sin modelo también)
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from app import config
from app.seguridad.citas import normalizar_cita
from app.servicios.chat import ErrorEntrada, ServicioChat

RUTA_HOJA = config.CARPETA_CASOS / "respuestas_esperadas.md"
RUTA_ESCENARIOS = config.CARPETA_CASOS / "escenarios.json"
RUTA_REPORTE_MD = config.BASE_DIR / "docs" / "resultados_evaluacion.md"
# Criterio por niveles, calibrado con paráfrasis del caso 02 (granite da similitudes
# altas y juntas: aciertos 0.86-0.94, hallazgos ajenos hasta 0.86). Citar la regla
# esperada es evidencia: baja el umbral. Solo el primero se cambia por línea de comandos.
UMBRAL_EMPAREJAR = 0.88          # similitud sola
UMBRAL_CON_POLITICA = 0.84       # + cita la misma política (POL-XX)
UMBRAL_CON_REGLA = 0.80          # + cita la regla exacta (POL-XX.N)
BONO_REGLA = 0.05                # solo para ordenar a quién se asigna primero
BONO_POLITICA = 0.02

Embedder = Callable[[Sequence[str]], np.ndarray]


# ---------------------------------------------------------------------------
# Hoja de respuestas
# ---------------------------------------------------------------------------
@dataclass
class Esperado:
    caso: str
    numero: int
    texto: str
    nivel: str                      # basico | avanzado
    referencia: str
    pols: list[str]
    seguridad: bool


def leer_hoja(ruta: Path = RUTA_HOJA) -> dict[str, list[Esperado]]:
    casos: dict[str, list[Esperado]] = {}
    caso = None
    for linea in ruta.read_text("utf-8").splitlines():
        if m := re.match(r"^## Caso (\d{2})", linea):
            caso = m.group(1)
            casos[caso] = []
            continue
        if linea.startswith("## "):
            caso = None
        if not caso or not re.match(r"^\| \d+ \|", linea):
            continue
        celdas = [c.strip() for c in linea.strip().strip("|").split("|")]
        if len(celdas) != 6:
            raise ValueError(f"fila con {len(celdas)} columnas (se esperaban 6): {linea}")
        numero, texto, nivel, referencia, pols, seguridad = celdas
        casos[caso].append(Esperado(
            caso, int(numero), texto.replace("**", ""),
            "avanzado" if nivel.upper() == "A" else "basico", referencia,
            [normalizar_cita(p) for p in re.findall(r"POL-\d{2}\.\d+", pols)],
            seguridad.lower().startswith("s")))
    return casos


# ---------------------------------------------------------------------------
# Emparejamiento
# ---------------------------------------------------------------------------
@dataclass
class Par:
    esperado: Esperado
    hallazgo: dict[str, Any] | None
    puntaje: float = 0.0
    cita_regla: bool = False
    cita_politica: bool = False
    seguridad_ok: bool | None = None


def _texto_hallazgo(h: dict[str, Any]) -> str:
    return f"{h.get('titulo', '')}. {h.get('descripcion', '')}"


def emparejar(esperados: list[Esperado], hallazgos: list[dict[str, Any]], embedder: Embedder,
              umbral: float = UMBRAL_EMPAREJAR) -> tuple[list[Par], list[dict[str, Any]]]:
    """Asignación 1 a 1, de mayor a menor puntaje. Devuelve (pares, no esperados)."""
    if not esperados:
        return [], list(hallazgos)
    if not hallazgos:
        return [Par(e, None) for e in esperados], []
    a = np.asarray(embedder([f"{e.texto} ({e.referencia})" for e in esperados]), dtype="float32")
    b = np.asarray(embedder([_texto_hallazgo(h) for h in hallazgos]), dtype="float32")
    a /= np.clip(np.linalg.norm(a, axis=1, keepdims=True), 1e-9, None)
    b /= np.clip(np.linalg.norm(b, axis=1, keepdims=True), 1e-9, None)
    sim = a @ b.T
    candidatos: list[tuple[float, int, int]] = []
    for i, e in enumerate(esperados):
        politicas = {p.split(".")[0] for p in e.pols}
        for j, h in enumerate(hallazgos):
            fuentes = {normalizar_cita(f) for f in h.get("fuentes", [])}
            s = float(sim[i, j])
            if fuentes & set(e.pols):
                elegible, orden = s >= min(umbral, UMBRAL_CON_REGLA), s + BONO_REGLA
            elif {f.split(".")[0] for f in fuentes} & politicas:
                elegible, orden = s >= min(umbral, UMBRAL_CON_POLITICA), s + BONO_POLITICA
            else:
                elegible, orden = s >= umbral, s
            if elegible:
                candidatos.append((orden, i, j))
    candidatos.sort(reverse=True)
    usados_e: set[int] = set()
    usados_h: set[int] = set()
    asignados: dict[int, tuple[int, float]] = {}
    for puntaje, i, j in candidatos:
        if i in usados_e or j in usados_h:
            continue
        usados_e.add(i)
        usados_h.add(j)
        asignados[i] = (j, puntaje)
    pares = []
    for i, e in enumerate(esperados):
        if i not in asignados:
            pares.append(Par(e, None))
            continue
        j, puntaje = asignados[i]
        h = hallazgos[j]
        fuentes = {normalizar_cita(f) for f in h.get("fuentes", [])}
        pares.append(Par(
            e, h, round(puntaje, 4),
            cita_regla=bool(fuentes & set(e.pols)),
            cita_politica=bool({f.split(".")[0] for f in fuentes} & {p.split(".")[0] for p in e.pols}),
            seguridad_ok=(bool(h.get("requiere_seguridad")) == e.seguridad)))
    no_esperados = [h for j, h in enumerate(hallazgos) if j not in usados_h]
    return pares, no_esperados


# ---------------------------------------------------------------------------
# Escenarios
# ---------------------------------------------------------------------------
@dataclass
class ResultadoEscenario:
    id: str
    grupo: str
    estado: str                     # ok | falla | omitido
    detalle: str = ""
    ruta: str = ""
    latencia_ms: int = 0


def _mensaje(escenario: dict[str, Any]) -> str:
    if "archivo" in escenario:
        return (config.CARPETA_CASOS / escenario["archivo"]).read_text("utf-8")
    return escenario.get("mensaje", "")


def evaluar_escenario(servicio: ServicioChat, escenario: dict[str, Any], hay_modelo: bool,
                      sesion: str) -> ResultadoEscenario:
    esperado = escenario.get("esperado", {})
    base = ResultadoEscenario(escenario["id"], escenario.get("grupo", ""), "ok")
    if esperado.get("requiere_modelo") and not hay_modelo:
        base.estado, base.detalle = "omitido", "necesita el modelo (configura el .env)"
        return base
    try:
        r = servicio.responder(_mensaje(escenario), sesion_id=sesion, usuario_id="evaluador")
    except ErrorEntrada as exc:
        ok = bool(esperado.get("error"))
        return ResultadoEscenario(base.id, base.grupo, "ok" if ok else "falla",
                                  f"error de entrada: {exc}"[:160], "error")
    base.ruta, base.latencia_ms = r.ruta, r.latencia_ms
    fallas: list[str] = []
    if esperado.get("error"):
        fallas.append("se esperaba un error de entrada")
    ruta = esperado.get("ruta")
    if esperado.get("ruta_con_modelo") and hay_modelo:
        ruta = esperado["ruta_con_modelo"]
    if ruta and r.ruta != ruta:
        fallas.append(f"ruta {r.ruta} (se esperaba {ruta})")
    for clave in ("motivo", "faq_id"):
        if clave in esperado and getattr(r, clave) != esperado[clave]:
            fallas.append(f"{clave} {getattr(r, clave)!r} (se esperaba {esperado[clave]!r})")
    faltan = set(esperado.get("enmascarados", [])) - set(r.enmascarados)
    if faltan:
        fallas.append(f"no enmascaró: {', '.join(sorted(faltan))}")
    altas = sum(h["severidad"] == "alta" for h in r.hallazgos)
    if "max_altas" in esperado and altas > esperado["max_altas"]:
        fallas.append(f"{altas} hallazgos de severidad alta (máximo {esperado['max_altas']})")
    if "max_hallazgos" in esperado and len(r.hallazgos) > esperado["max_hallazgos"]:
        fallas.append(f"{len(r.hallazgos)} hallazgos (máximo {esperado['max_hallazgos']})")
    if config.AVISO_SEGURIDAD not in r.respuesta:
        fallas.append("falta el aviso de que no reemplaza a Seguridad")
    base.estado = "falla" if fallas else "ok"
    base.detalle = "; ".join(fallas) or (
        f"{len(r.hallazgos)} hallazgos, {altas} altos" if r.ruta == "modelo" else r.motivo or r.fuente)
    return base


# ---------------------------------------------------------------------------
# Casos
# ---------------------------------------------------------------------------
@dataclass
class ResultadoCaso:
    caso: str
    archivo: str
    ruta: str
    latencia_ms: int
    tokens: list[int]
    hallazgos_bot: int
    pares: list[Par] = field(default_factory=list)
    no_esperados: list[dict[str, Any]] = field(default_factory=list)
    revision_id: str | None = None

    def metricas(self) -> dict[str, Any]:
        return _metricas(self.pares, len(self.no_esperados), self.hallazgos_bot)


def _metricas(pares: list[Par], no_esperados: int, total_bot: int) -> dict[str, Any]:
    def det(nivel: str) -> tuple[int, int]:
        ps = [p for p in pares if p.esperado.nivel == nivel]
        return sum(p.hallazgo is not None for p in ps), len(ps)

    emparejados = [p for p in pares if p.hallazgo is not None]
    a_seguridad = [p for p in pares if p.esperado.seguridad]
    return {
        "basicos": det("basico"), "avanzados": det("avanzado"),
        "detectados": (len(emparejados), len(pares)),
        "no_esperados": no_esperados, "hallazgos_bot": total_bot,
        "cita_regla_exacta": (sum(p.cita_regla for p in emparejados), len(emparejados)),
        "cita_politica": (sum(p.cita_politica for p in emparejados), len(emparejados)),
        "seguridad_detectada": (sum(p.hallazgo is not None and bool(p.hallazgo.get("requiere_seguridad"))
                                    for p in a_seguridad), len(a_seguridad)),
        "seguridad_coincide": (sum(bool(p.seguridad_ok) for p in emparejados), len(emparejados)),
        # en una solicitud a alto nivel casi todo se deduce: debe presentarse "a confirmar"
        "a_confirmar": (sum(p.hallazgo.get("estado", "a_confirmar") != "confirmado"
                            for p in emparejados), len(emparejados)),
    }


def evaluar_caso(servicio: ServicioChat, caso: str, esperados: list[Esperado],
                 embedder: Embedder, umbral: float, sesion: str) -> ResultadoCaso:
    ruta = next(config.CARPETA_CASOS.glob(f"caso_{caso}_*.txt"))
    r = servicio.responder(ruta.read_text("utf-8"), sesion_id=sesion, usuario_id="evaluador")
    pares, no_esperados = emparejar(esperados, r.hallazgos, embedder, umbral)
    return ResultadoCaso(caso, ruta.name, r.ruta, r.latencia_ms, r.tokens, len(r.hallazgos),
                         pares, no_esperados, r.revision_id)


# ---------------------------------------------------------------------------
# Reporte
# ---------------------------------------------------------------------------
def _frac(par: tuple[int, int]) -> str:
    n, d = par
    return f"{n}/{d}" + (f" ({100 * n / d:.0f} %)" if d else "")


def _percentil(valores: list[int], p: float) -> int:
    if not valores:
        return 0
    orden = sorted(valores)
    return int(orden[min(len(orden) - 1, int(round(p * (len(orden) - 1))))])


def reporte_markdown(datos: dict[str, Any]) -> str:
    lineas = ["# Resultados de la evaluación", "",
              f"Generado por `python -m scripts.evaluar_casos` el {datos['fecha']}. "
              f"Modelo: `{datos['modelo']}`. Umbral de emparejamiento: {datos['umbral']}. "
              "Los emparejamientos son automáticos (similitud semántica + regla POL citada): "
              "revisar `emparejamientos.csv` antes de publicar las cifras.", ""]
    total = datos.get("total")
    if total:
        lineas += [
            "## Detección contra la hoja de respuestas", "",
            "| Métrica | Resultado |", "|---|---|",
            f"| Fallas básicas detectadas | {_frac(total['basicos'])} |",
            f"| Fallas avanzadas detectadas | {_frac(total['avanzados'])} |",
            f"| Hallazgos no esperados (posibles inventados, revisar) | {total['no_esperados']} de "
            f"{total['hallazgos_bot']} |",
            f"| Cita la regla POL esperada | {_frac(total['cita_regla_exacta'])} |",
            f"| Presentados como \"a confirmar\" (deducidos de la solicitud) | "
            f"{_frac(total.get('a_confirmar', (0, 0)))} |",
            f"| Cita la política POL esperada | {_frac(total['cita_politica'])} |",
            f"| Puntos de Seguridad marcados \"va a Seguridad\" | {_frac(total['seguridad_detectada'])} |",
            f"| Latencia por caso p50 / p95 | {datos['latencia']['p50']} / {datos['latencia']['p95']} ms |",
            f"| Tokens por caso (entrada / salida, promedio) | {datos['tokens']['entrada']} / "
            f"{datos['tokens']['salida']} |", "",
            "| Caso | Básicos | Avanzados | No esperados | Regla POL | Latencia |",
            "|---|---|---|---|---|---|",
        ]
        for c in datos["casos"]:
            m = c["metricas"]
            lineas.append(f"| {c['archivo']} | {_frac(m['basicos'])} | {_frac(m['avanzados'])} | "
                          f"{m['no_esperados']} | {_frac(m['cita_regla_exacta'])} | "
                          f"{c['latencia_ms']} ms |")
        lineas.append("")
    else:
        lineas += ["## Detección contra la hoja de respuestas", "",
                   f"No evaluado: {datos.get('motivo_sin_casos', 'sin modelo')}.", ""]
    lineas += ["## Escenarios", "", "| Escenario | Grupo | Resultado | Detalle |", "|---|---|---|---|"]
    for e in datos["escenarios"]:
        marca = {"ok": "ok", "falla": "**falla**", "omitido": "omitido"}[e["estado"]]
        lineas.append(f"| {e['id']} | {e['grupo']} | {marca} | {e['detalle']} |")
    resumen = datos["resumen_escenarios"]
    lineas += ["", f"Escenarios: {resumen['ok']} ok, {resumen['falla']} con falla, "
                   f"{resumen['omitido']} omitidos.", "",
               "> " + config.AVISO_SEGURIDAD, ""]
    return "\n".join(lineas)


def escribir_salidas(datos: dict[str, Any], casos: list[ResultadoCaso], carpeta: Path) -> None:
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / "resultados.json").write_text(json.dumps(datos, ensure_ascii=False, indent=2,
                                                        default=str) + "\n", "utf-8")
    with (carpeta / "emparejamientos.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["caso", "esperado_n", "nivel", "esperado", "pol_esperadas", "seguridad_esperada",
                    "hallazgo_bot", "estado_bot", "fuentes_bot", "requiere_seguridad_bot", "puntaje",
                    "cita_regla", "cita_politica"])
        for c in casos:
            for p in c.pares:
                h = p.hallazgo or {}
                w.writerow([c.caso, p.esperado.numero, p.esperado.nivel, p.esperado.texto,
                            "; ".join(p.esperado.pols), "si" if p.esperado.seguridad else "no",
                            h.get("titulo", ""), h.get("estado", ""), "; ".join(h.get("fuentes", [])),
                            "" if not h else ("si" if h.get("requiere_seguridad") else "no"),
                            p.puntaje or "", "si" if p.cita_regla else "", "si" if p.cita_politica else ""])
            for h in c.no_esperados:
                w.writerow([c.caso, "", "", "(no esperado)", "", "", h.get("titulo", ""),
                            h.get("estado", ""), "; ".join(h.get("fuentes", [])),
                            "si" if h.get("requiere_seguridad") else "no", "", "", ""])
    RUTA_REPORTE_MD.parent.mkdir(parents=True, exist_ok=True)
    RUTA_REPORTE_MD.write_text(reporte_markdown(datos), "utf-8")


# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None, servicio: ServicioChat | None = None,
         embedder: Embedder | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evalúa al bot contra los casos de prueba.")
    parser.add_argument("--casos", default="01,02,03", help="casos a correr, p. ej. 01,03")
    parser.add_argument("--sin-casos", action="store_true", help="solo escenarios")
    parser.add_argument("--umbral", type=float, default=UMBRAL_EMPAREJAR,
                        help="puntaje mínimo para emparejar un hallazgo con uno esperado")
    parser.add_argument("--salida", type=Path, default=None,
                        help="carpeta de resultados (por defecto data/evaluacion/<fecha>)")
    args = parser.parse_args(argv)

    marca = time.strftime("%Y%m%d-%H%M%S")
    carpeta = args.salida or (config.BASE_DIR / "data" / "evaluacion" / marca)
    if servicio is None:
        # base propia por corrida: no mezcla sus turnos con las métricas del chat
        carpeta.mkdir(parents=True, exist_ok=True)
        config.RUTA_DB = carpeta / "revisiones.db"
        config.CARPETA_INFORMES = carpeta / "informes"
        servicio = ServicioChat()
    endpoints = getattr(servicio.router, "endpoints", None)
    hay_modelo = bool(endpoints) if endpoints is not None else True
    modelo = (getattr(endpoints[0], "modelo", "") if endpoints else "") or (
        "sin modelo (modo demo)" if not hay_modelo else "router de pruebas")
    print(f"Modelo: {modelo}")

    escenarios = json.loads(RUTA_ESCENARIOS.read_text("utf-8"))["escenarios"]
    resultados_esc = []
    for esc in escenarios:
        r = evaluar_escenario(servicio, esc, hay_modelo, f"eval-{esc['id']}-{marca}")
        resultados_esc.append(r)
        print(f"  [{r.estado:<7}] {r.id:<24} {r.detalle}")

    datos: dict[str, Any] = {
        "fecha": time.strftime("%Y-%m-%d %H:%M"), "modelo": modelo, "umbral": args.umbral,
        "escenarios": [asdict(r) for r in resultados_esc],
        "resumen_escenarios": {e: sum(r.estado == e for r in resultados_esc)
                               for e in ("ok", "falla", "omitido")},
        "casos": [],
    }
    casos: list[ResultadoCaso] = []
    if args.sin_casos:
        datos["motivo_sin_casos"] = "se pidió --sin-casos"
    elif not hay_modelo:
        datos["motivo_sin_casos"] = ("no hay modelo configurado; define LLM_BASE_URL, LLM_API_KEY y "
                                     "LLM_MODEL en el .env")
        print(f"\nCasos no evaluados: {datos['motivo_sin_casos']}.")
    else:
        if embedder is None:
            from app.rag.embeddings import embed_granite as embedder
        hoja = leer_hoja()
        for caso in [c.strip().zfill(2) for c in args.casos.split(",") if c.strip()]:
            print(f"\nCaso {caso}: revisando…")
            rc = evaluar_caso(servicio, caso, hoja[caso], embedder, args.umbral,
                              f"eval-caso{caso}-{marca}")
            casos.append(rc)
            m = rc.metricas()
            print(f"  ruta {rc.ruta} · {rc.hallazgos_bot} hallazgos · básicos {_frac(m['basicos'])} · "
                  f"avanzados {_frac(m['avanzados'])} · no esperados {m['no_esperados']} · "
                  f"regla POL {_frac(m['cita_regla_exacta'])} · {rc.latencia_ms} ms")
        todos = [p for c in casos for p in c.pares]
        datos["total"] = _metricas(todos, sum(len(c.no_esperados) for c in casos),
                                   sum(c.hallazgos_bot for c in casos))
        latencias = [c.latencia_ms for c in casos]
        datos["latencia"] = {"p50": _percentil(latencias, 0.5), "p95": _percentil(latencias, 0.95)}
        datos["tokens"] = {"entrada": int(statistics.mean(c.tokens[0] for c in casos)) if casos else 0,
                           "salida": int(statistics.mean(c.tokens[1] for c in casos)) if casos else 0}
        datos["casos"] = [{"caso": c.caso, "archivo": c.archivo, "ruta": c.ruta,
                           "revision_id": c.revision_id, "latencia_ms": c.latencia_ms,
                           "tokens": c.tokens, "metricas": c.metricas()} for c in casos]

    escribir_salidas(datos, casos, carpeta)
    print(f"\nResultados: {carpeta}  ·  resumen: docs/{RUTA_REPORTE_MD.name}")
    return 1 if datos["resumen_escenarios"]["falla"] else 0


if __name__ == "__main__":
    sys.exit(main())
