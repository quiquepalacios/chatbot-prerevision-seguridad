"""scripts/indexar.py de punta a punta, sin red: embedder falso y carpetas temporales."""
from __future__ import annotations

import json
import shutil

from app import config
from scripts import indexar
from tests.test_indice import embedder_falso


def test_indexar_construye_indice_y_reportes(tmp_path, monkeypatch):
    conocimiento = tmp_path / "conocimiento"
    (conocimiento / "lineamientos").mkdir(parents=True)
    shutil.copy(config.CARPETA_LINEAMIENTOS / "politicas_seguridad_banco_andesur.md",
                conocimiento / "lineamientos")
    guias = conocimiento / "fuentes" / "cheatsheets"
    guias.mkdir(parents=True)
    oraciones = " ".join(f"Rule {i} says every request must be authorized at the server." for i in range(40))
    (guias / "Authorization_Cheat_Sheet.md").write_text(
        f"# Authorization Cheat Sheet\n\n## Enforce on the server\n\n{oraciones}\n", "utf-8")
    (guias / "Authorization_Cheat_Sheet.pdf").write_bytes(b"%PDF mismo documento")  # otro formato
    (guias / "caso_01_prueba.md").write_text("# Caso\n\n" + oraciones, "utf-8")      # excluido

    monkeypatch.setattr(config, "CARPETA_CONOCIMIENTO", conocimiento)
    monkeypatch.setattr(config, "CHROMA_DIR", tmp_path / "chroma")
    monkeypatch.setattr(indexar, "RUTA_REPORTE_MD", tmp_path / "docs" / "reporte.md")

    assert indexar.main([], embedder=embedder_falso) == 0

    reporte = json.loads((tmp_path / "chroma" / "reporte_indice.json").read_text("utf-8"))
    total = reporte["estadisticas"]["total"]
    assert total["documentos"] == 2
    assert reporte["estadisticas"]["por_grupo"]["Lineamientos LSA-2026 (ficticio)"]["fragmentos"] == 73
    assert total["palabras_max"] <= config.CHUNK_MAX_PALABRAS
    assert reporte["excluidos"][0]["archivo"].endswith("caso_01_prueba.md")
    assert reporte["otro_formato"][0]["archivo"].endswith("Authorization_Cheat_Sheet.pdf")
    assert "acierto" in reporte["control"]

    md = (tmp_path / "docs" / "reporte.md").read_text("utf-8")
    assert "**Total: 2 documentos" in md and "## Limpieza" in md

    from app.rag.indice import IndiceLineamientos
    ix = IndiceLineamientos(embedder=embedder_falso)
    assert ix.info()["fragmentos"] == total["fragmentos"]
    assert ix.info()["documentos"] == 2
    ix._cliente = None


def test_simular_no_toca_chroma(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHROMA_DIR", tmp_path / "chroma")
    assert indexar.main(["--simular"], embedder=embedder_falso) == 0
    assert not (tmp_path / "chroma").exists()
