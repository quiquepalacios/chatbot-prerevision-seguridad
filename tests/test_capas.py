"""Las tres capas: originales -> datos limpios (procesado/) -> fragmentos. Sin red."""
from __future__ import annotations

import json
import shutil

from app import config
from app.rag import cargadores, procesado
from scripts import indexar
from tests.test_indice import embedder_falso

ORACIONES = " ".join(f"Rule {i} says every request must be authorized at the server." for i in range(30))


def _conocimiento(tmp_path):
    conocimiento = tmp_path / "conocimiento"
    (conocimiento / "lineamientos").mkdir(parents=True)
    shutil.copy(config.CARPETA_LINEAMIENTOS / "politicas_seguridad_banco_andesur.md",
                conocimiento / "lineamientos")
    guias = conocimiento / "fuentes" / "cheatsheets"
    guias.mkdir(parents=True)
    (guias / "Authorization_Cheat_Sheet.md").write_text(
        f"# Authorization Cheat Sheet\n\n![x](a.png)\n\n## Enforce\n\n{ORACIONES}\n", "utf-8")
    return conocimiento


def test_guardar_y_leer_conservan_el_documento(tmp_path):
    original = config.CARPETA_LINEAMIENTOS / "politicas_seguridad_banco_andesur.md"
    doc = cargadores.cargar(original)
    destino = procesado.ruta_para(tmp_path, "lineamientos/politicas.md")
    procesado.guardar(doc, destino, "lineamientos/politicas.md", procesado.sha256(original), "ficticio")
    assert destino.name == "politicas.md.json"
    leido, meta = procesado.leer(destino)
    assert [b.texto for b in leido.bloques] == [b.texto for b in doc.bloques]
    assert leido.bloques[0].cita == "POL-01.1" and leido.bloques[0].atomico
    assert leido.bloques[0].extra["politica"] == "POL-01"
    assert (leido.fuente, leido.tipo) == (doc.fuente, doc.tipo)
    assert meta["sha256_original"] == procesado.sha256(original) and meta["licencia"] == "ficticio"
    assert procesado.vigente(destino, procesado.sha256(original))
    assert not procesado.vigente(destino, "otra-huella")


def test_preparar_escribe_la_capa_limpia_y_la_reutiliza(tmp_path):
    conocimiento = _conocimiento(tmp_path)
    fragmentos, datos = indexar.preparar(conocimiento, {}, avisar=lambda _: None)
    limpios = sorted(p.relative_to(conocimiento).as_posix()
                     for p in (conocimiento / "procesado").rglob("*.json"))
    assert limpios == ["procesado/fuentes/cheatsheets/Authorization_Cheat_Sheet.md.json",
                       "procesado/lineamientos/politicas_seguridad_banco_andesur.md.json"]
    assert datos["capas"]["procesados_ahora"] == 2 and datos["capas"]["limpios"] == 2
    cheat = json.loads((conocimiento / limpios[0]).read_text("utf-8"))
    assert cheat["limpieza"]["imagenes"] == 1 and "png" not in json.dumps(cheat["bloques"])
    assert len(fragmentos) > 73

    _, datos = indexar.preparar(conocimiento, {}, avisar=lambda _: None)
    assert datos["capas"]["reutilizados"] == 2 and datos["capas"]["procesados_ahora"] == 0
    _, datos = indexar.preparar(conocimiento, {}, avisar=lambda _: None, reprocesar=True)
    assert datos["capas"]["procesados_ahora"] == 2


def test_el_indice_sale_de_la_capa_limpia(tmp_path):
    """Si se edita el JSON limpio (sin tocar el original), el fragmento cambia."""
    conocimiento = _conocimiento(tmp_path)
    indexar.preparar(conocimiento, {}, avisar=lambda _: None)
    ruta = conocimiento / "procesado" / "lineamientos" / "politicas_seguridad_banco_andesur.md.json"
    datos = json.loads(ruta.read_text("utf-8"))
    datos["bloques"][0]["texto"] = "POL-01.1 TEXTO MARCADO DESDE LA CAPA LIMPIA para la prueba."
    ruta.write_text(json.dumps(datos, ensure_ascii=False), "utf-8")
    fragmentos, _ = indexar.preparar(conocimiento, {}, avisar=lambda _: None)
    assert any("MARCADO DESDE LA CAPA LIMPIA" in f.texto for f in fragmentos)


def test_original_modificado_se_vuelve_a_limpiar_y_huerfanos_se_borran(tmp_path):
    conocimiento = _conocimiento(tmp_path)
    indexar.preparar(conocimiento, {}, avisar=lambda _: None)
    guia = conocimiento / "fuentes" / "cheatsheets" / "Authorization_Cheat_Sheet.md"
    guia.write_text(guia.read_text("utf-8") + "\nNew rule: deny by default in every service.\n", "utf-8")
    _, datos = indexar.preparar(conocimiento, {}, avisar=lambda _: None)
    assert datos["capas"]["procesados_ahora"] == 1 and datos["capas"]["reutilizados"] == 1

    guia.unlink()
    _, datos = indexar.preparar(conocimiento, {}, avisar=lambda _: None)
    assert datos["capas"]["eliminados"] == 1
    assert not list((conocimiento / "procesado" / "fuentes").rglob("*.json"))


def test_indexar_reporta_las_tres_capas(tmp_path, monkeypatch):
    conocimiento = _conocimiento(tmp_path)
    monkeypatch.setattr(config, "CARPETA_CONOCIMIENTO", conocimiento)
    monkeypatch.setattr(config, "CHROMA_DIR", tmp_path / "chroma")
    monkeypatch.setattr(indexar, "RUTA_REPORTE_MD", tmp_path / "reporte.md")
    assert indexar.main(["--sin-control"], embedder=embedder_falso) == 0
    md = (tmp_path / "reporte.md").read_text("utf-8")
    assert "## Tres capas de datos" in md and "2. Datos limpios" in md
    reporte = json.loads((tmp_path / "chroma" / "reporte_indice.json").read_text("utf-8"))
    assert reporte["capas"]["limpios"] == 2


def test_el_repositorio_trae_originales_y_limpios_en_sincronia():
    """Cada original versionado tiene su JSON limpio con la misma huella."""
    conocimiento = config.CARPETA_CONOCIMIENTO
    limpios = config.CARPETA_PROCESADO
    if not limpios.exists():                       # todavía no se indexó en este equipo
        return
    for ruta in cargadores.archivos_de(conocimiento):
        if limpios in ruta.parents:
            continue
        relativo = ruta.relative_to(conocimiento).as_posix()
        destino = procesado.ruta_para(limpios, relativo)
        assert destino.exists(), f"falta la capa limpia de {relativo}"
        assert procesado.vigente(destino, procesado.sha256(ruta)), f"{relativo} cambió: reindexar"
