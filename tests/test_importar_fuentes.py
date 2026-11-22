"""Pruebas de scripts/importar_fuentes.py con carpetas temporales (sin red)."""
from __future__ import annotations

import json
import zipfile

import pytest

from scripts import importar_fuentes as imp

TEXTO = " ".join(["La API debe validar en el servidor que el recurso pertenece al usuario."] * 10)
REGLAS = {
    "excluir": [{"patron": "caso_*", "motivo": "caso de prueba"},
                {"patron": "*Scribd*", "motivo": "copia no oficial"}],
    "zip": {"sitio.zip": {"incluir": ["site/cheatsheets/*.html"]}},
    "licencias": [{"patron": "*Cheat_Sheet*", "licencia": "CC BY-SA 4.0"}],
}


@pytest.fixture()
def carpetas(tmp_path):
    origen, conocimiento = tmp_path / "origen", tmp_path / "conocimiento"
    (conocimiento / "fuentes" / "cheatsheets").mkdir(parents=True)
    (conocimiento / "lineamientos").mkdir()
    origen.mkdir()
    return origen, conocimiento


def _estados(decisiones):
    return {d.origen.replace("\\", "/"): d.estado for d in decisiones}


def test_decide_cada_archivo_segun_las_reglas(carpetas):
    origen, conocimiento = carpetas
    (conocimiento / "fuentes" / "cheatsheets" / "Logging_Cheat_Sheet.md").write_text(TEXTO, "utf-8")
    (conocimiento / "lineamientos" / "politicas.md").write_text(TEXTO + " POL", "utf-8")

    (origen / "guia_interna.md").write_text("# Guía\n\n" + TEXTO, "utf-8")     # nueva
    (origen / "copia_politicas.md").write_text(TEXTO + " POL", "utf-8")       # idéntica
    (origen / "caso_01.txt").write_text(TEXTO, "utf-8")                      # excluida
    (origen / "AWS-Scribd.pdf").write_bytes(b"%PDF")                         # excluida
    (origen / "planilla.xlsx").write_bytes(b"x")                             # sin cargador
    (origen / "vacio.txt").write_text("hola", "utf-8")                       # sin texto
    with zipfile.ZipFile(origen / "sitio.zip", "w") as z:
        z.writestr("site/cheatsheets/Logging_Cheat_Sheet.html", f"<p>{TEXTO}</p>")
        z.writestr("site/cheatsheets/Session_Management_Cheat_Sheet.html",
                   f"<h1>Session Management</h1><p>{TEXTO}</p>")
        z.writestr("site/assets/logo.png", b"png")                           # fuera de la selección
        z.writestr("../../escapa.html", f"<p>{TEXTO}</p>")                   # fuera de la selección

    decisiones, resultados = imp.importar(origen, conocimiento, REGLAS)
    estados = _estados(decisiones)
    assert estados == {
        "AWS-Scribd.pdf": "excluido",
        "caso_01.txt": "excluido",
        "copia_politicas.md": "duplicado",
        "guia_interna.md": "importado",
        "planilla.xlsx": "no soportado",
        "sitio.zip:site/cheatsheets/Logging_Cheat_Sheet.html": "otro formato",
        "sitio.zip:site/cheatsheets/Session_Management_Cheat_Sheet.html": "importado",
        "vacio.txt": "sin texto",
    }
    propias = conocimiento / "fuentes" / "propias"
    assert sorted(p.name for p in propias.iterdir()) == [
        "Session_Management_Cheat_Sheet.html", "guia_interna.md"]
    assert not (conocimiento.parent / "escapa.html").exists()
    licencias = {r.fuente.nombre: r.fuente.licencia for r in resultados}
    assert licencias["Session_Management_Cheat_Sheet.html"] == "CC BY-SA 4.0"
    assert licencias["guia_interna.md"] == imp.SIN_LICENCIA


def test_sin_descargas_previas_usa_las_fuentes_locales(carpetas):
    """Clon limpio sin red: el PDF o DOCX local de ASVS entra en lugar del CSV."""
    origen, conocimiento = carpetas
    docx = origen / "OWASP_ASVS_5.0.0_en.docx"
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml",
                   f"<w:document><w:p><w:r><w:t>{TEXTO}</w:t></w:r></w:p></w:document>")
    decisiones, _ = imp.importar(origen, conocimiento, REGLAS)
    assert _estados(decisiones) == {"OWASP_ASVS_5.0.0_en.docx": "importado"}

    # si después se descarga el CSV oficial, el DOCX pasa a ser "otro formato"
    (conocimiento / "fuentes" / "propias" / docx.name).unlink()
    (conocimiento / "fuentes" / "asvs").mkdir()
    (conocimiento / "fuentes" / "asvs" / "OWASP_ASVS_5.0.0_en.csv").write_text("x", "utf-8")
    decisiones, _ = imp.importar(origen, conocimiento, REGLAS)
    assert _estados(decisiones) == {"OWASP_ASVS_5.0.0_en.docx": "otro formato"}


def test_simular_no_copia_nada(carpetas):
    origen, conocimiento = carpetas
    (origen / "guia.md").write_text(TEXTO, "utf-8")
    decisiones, _ = imp.importar(origen, conocimiento, REGLAS, simular=True)
    assert decisiones[0].estado == "importado"
    assert not (conocimiento / "fuentes" / "propias").exists()


def test_main_escribe_manifiesto_y_rechaza_origen_dentro_de_conocimiento(
        carpetas, monkeypatch, tmp_path):
    origen, conocimiento = carpetas
    from app import config

    monkeypatch.setattr(config, "CARPETA_CONOCIMIENTO", conocimiento)
    reglas = tmp_path / "reglas.json"
    reglas.write_text(json.dumps(REGLAS), "utf-8")
    (origen / "guia.md").write_text(TEXTO, "utf-8")

    assert imp.main(["--desde", str(origen), "--reglas", str(reglas)]) == 0
    manifiesto = json.loads((conocimiento / "fuentes" / "manifiesto.json").read_text("utf-8"))
    assert manifiesto["archivos"][0]["archivo"] == "propias/guia.md"
    assert manifiesto["archivos"][0]["url"] == "local:guia.md"

    assert imp.main(["--desde", str(conocimiento / "fuentes"), "--reglas", str(reglas)]) == 2
