"""scripts/evaluar_casos.py sin red: hoja real, embedder falso y RouterFalso."""
from __future__ import annotations

import json

from app import config
from scripts import evaluar_casos as ev
from tests.conftest import llamada, respuesta
from tests.test_indice import embedder_falso


def test_hoja_de_respuestas_tiene_60_hallazgos_con_su_regla():
    hoja = ev.leer_hoja()
    todos = [e for c in hoja.values() for e in c]
    assert [len(hoja[c]) for c in ("01", "02", "03")] == [21, 18, 21]
    assert sum(e.nivel == "basico" for e in todos) == 42
    assert sum(e.nivel == "avanzado" for e in todos) == 18
    assert sum(e.seguridad for e in todos) == 32
    assert all(e.pols for e in todos)                         # todos tienen POL
    primero = hoja["01"][0]
    assert primero.pols == ["POL-01.1", "POL-01.2"] and primero.seguridad
    assert "**" not in hoja["01"][14].texto                   # sin marcas de negrita


def _esperado(n, texto, pols, nivel="basico", seguridad=True):
    return ev.Esperado("99", n, texto, nivel, "API1", pols, seguridad)


def test_emparejar_uno_a_uno_y_mide_citas():
    esperados = [_esperado(1, "endpoint de ubicacion sin validar dueño del vehiculo", ["POL-01.1"]),
                 _esperado(2, "contraseñas guardadas en texto plano", ["POL-10.1"], "avanzado")]
    bot = [{"titulo": "ubicacion sin validar dueño", "descripcion": "endpoint de ubicacion del vehiculo",
            "fuentes": ["POL-01.1"], "requiere_seguridad": True},
           {"titulo": "otra cosa", "descripcion": "kafka sin particiones", "fuentes": ["A03:2025"]}]
    pares, extra = ev.emparejar(esperados, bot, embedder_falso, umbral=0.5)
    assert pares[0].hallazgo is bot[0] and pares[0].cita_regla and pares[0].seguridad_ok
    assert pares[1].hallazgo is None and extra == [bot[1]]
    m = ev._metricas(pares, len(extra), len(bot))
    assert m["basicos"] == (1, 1) and m["avanzados"] == (0, 1) and m["no_esperados"] == 1
    assert m["cita_regla_exacta"] == (1, 1) and m["seguridad_detectada"] == (1, 2)


def test_citar_la_regla_baja_el_umbral():
    """Similitud 0.82: no alcanza sola (0.88) ni con la política (0.84); sí con la regla (0.80)."""
    import numpy as np

    def fijo(textos):
        return np.array([[1.0, 0.0] if "(API1)" in t else [0.82, (1 - 0.82 ** 2) ** 0.5]
                         for t in textos], dtype="float32")

    esperado = [_esperado(1, "ubicación sin validar dueño", ["POL-01.1"])]
    base = {"titulo": "algo parecido", "descripcion": "", "requiere_seguridad": True}
    resultado = {}
    for nombre, fuentes in {"sin_cita": [], "politica": ["POL-01.6"], "regla": ["POL-01.1"]}.items():
        pares, _ = ev.emparejar(esperado, [dict(base, fuentes=fuentes)], fijo)
        resultado[nombre] = pares[0].hallazgo is not None
    assert resultado == {"sin_cita": False, "politica": False, "regla": True}


def test_escenarios_sin_modelo(servicio, tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "RUTA_REPORTE_MD", tmp_path / "resultados.md")
    servicio.router_falso.fallar = True
    servicio.router.endpoints = []                            # como si no hubiera .env
    assert ev.main(["--salida", str(tmp_path / "eval")], servicio=servicio,
                   embedder=embedder_falso) == 0
    datos = json.loads((tmp_path / "eval" / "resultados.json").read_text("utf-8"))
    estados = {e["id"]: e["estado"] for e in datos["escenarios"]}
    assert estados["trampa_bien_disenada"] == "omitido"
    assert all(v == "ok" for k, v in estados.items() if k != "trampa_bien_disenada"), estados
    assert "no hay modelo" in datos["motivo_sin_casos"]
    md = (tmp_path / "resultados.md").read_text("utf-8")
    assert "No evaluado" in md and config.AVISO_SEGURIDAD in md


def test_caso_completo_con_router_falso(servicio, tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "RUTA_REPORTE_MD", tmp_path / "resultados.md")
    hoja = ev.leer_hoja()
    exactos = [{"titulo": e.texto, "descripcion": e.texto, "fuentes": e.pols[:1],
                "severidad": "alta", "requiere_seguridad": e.seguridad,
                "nivel": e.nivel} for e in hoja["02"][:5]]
    inventado = {"titulo": "Kafka sin particiones", "descripcion": "x", "fuentes": ["A03:2025"],
                 "severidad": "baja", "requiere_seguridad": False, "nivel": "basico"}
    servicio.router_falso.respuestas = [
        respuesta(tool_calls=[llamada("registrar_revision", solucion="Constancias",
                                      hallazgos=exactos + [inventado])]),
        respuesta("Listo."),
    ]
    assert ev.main(["--casos", "02", "--sin-casos"], servicio=servicio,
                   embedder=embedder_falso) in (0, 1)        # --sin-casos: solo escenarios
    servicio.router_falso.respuestas = [
        respuesta(tool_calls=[llamada("registrar_revision", solucion="Constancias",
                                      hallazgos=exactos + [inventado])]),
        respuesta("Listo."),
    ]
    caso = ev.evaluar_caso(servicio, "02", hoja["02"], embedder_falso, 0.9, "s-eval")
    m = caso.metricas()
    assert m["detectados"] == (5, 18) and m["no_esperados"] == 1
    assert m["cita_regla_exacta"] == (5, 5) and caso.revision_id
    ev.escribir_salidas({"fecha": "hoy", "modelo": "falso", "umbral": 0.9, "escenarios": [],
                         "resumen_escenarios": {"ok": 0, "falla": 0, "omitido": 0},
                         "casos": [{"archivo": caso.archivo, "latencia_ms": 1, "metricas": m}],
                         "total": m, "latencia": {"p50": 1, "p95": 1},
                         "tokens": {"entrada": 1, "salida": 1}}, [caso], tmp_path / "salida")
    filas = (tmp_path / "salida" / "emparejamientos.csv").read_text("utf-8-sig").splitlines()
    assert len(filas) == 1 + 18 + 1 and "(no esperado)" in filas[-1]
    assert "Fallas básicas detectadas | 5/13" in (tmp_path / "resultados.md").read_text("utf-8")
