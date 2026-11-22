"""Pruebas de scripts/descargar_fuentes.py sin red: httpx.MockTransport responde."""
from __future__ import annotations

import json

import httpx
import pytest

from scripts import descargar_fuentes as df

PDF = b"%PDF-1.7\n" + b"x" * 500
CSV = ("chapter_id,chapter_name,section_id,section_name,req_id,req_description,L\n"
       + "V1,Encoding,V1.1,Arquitectura,V1.1.1,Verify that input is decoded once.,2\n" * 5).encode()
MD = ("# API1:2023 Broken Object Level Authorization\n\n" + "texto " * 100).encode()


def _cliente(respuestas: dict[str, tuple[int, bytes]]) -> httpx.Client:
    def manejar(peticion: httpx.Request) -> httpx.Response:
        codigo, cuerpo = respuestas.get(str(peticion.url), (404, b"no existe"))
        return httpx.Response(codigo, content=cuerpo)

    return httpx.Client(transport=httpx.MockTransport(manejar))


def _fuente(tipo: str, nombre: str = "") -> df.Fuente:
    nombre = nombre or f"archivo.{tipo}"
    return df.Fuente("grupo", nombre, f"https://ejemplo.test/{nombre}", "CC BY-SA 4.0", tipo)


def test_catalogo_tiene_las_35_fuentes_y_urls_fijadas():
    fuentes = df.catalogo()
    assert len(fuentes) == 35
    por_grupo = {g: sum(f.grupo == g for f in fuentes) for g in df.GRUPOS}
    assert por_grupo == {"asvs": 1, "api_top10_2023": 10, "top10_2025_es": 10,
                         "cheatsheets": 10, "nist": 4}
    # las de GitHub apuntan a un commit, no a una rama que cambia
    for f in fuentes:
        if "raw.githubusercontent.com" in f.url:
            assert "/master/" not in f.url and "/main/" not in f.url
        assert f.url.startswith("https://")


@pytest.mark.parametrize("tipo,contenido", [
    ("pdf", b"<html>" + b"x" * 300),                        # página de error, no un PDF
    ("csv", b"otra,cabecera\n" + b"x" * 300),               # CSV que no es ASVS
    ("md", b"<!DOCTYPE html><html>" + b"x" * 300),          # HTML en vez de Markdown
    ("md", b"corto"),                                        # truncado
])
def test_validar_rechaza_contenido_inesperado(tipo, contenido):
    with pytest.raises(df.ContenidoInvalido):
        df.validar(_fuente(tipo), contenido)


def test_descarga_valida_y_escribe_manifiesto(tmp_path):
    fuentes = [_fuente("pdf"), _fuente("csv"), _fuente("md")]
    respuestas = {fuentes[0].url: (200, PDF), fuentes[1].url: (200, CSV),
                  fuentes[2].url: (200, MD)}
    with _cliente(respuestas) as cliente:
        resultados = df.descargar(fuentes, tmp_path, cliente, avisar=lambda _: None)

    assert [r.estado for r in resultados] == ["descargado"] * 3
    assert (tmp_path / "grupo" / "archivo.pdf").read_bytes() == PDF
    assert not list(tmp_path.rglob("*.parcial"))             # nada a medias

    manifiesto = json.loads(df.escribir_manifiesto(resultados, tmp_path).read_text("utf-8"))
    assert len(manifiesto["archivos"]) == 3
    assert all(len(a["sha256"]) == 64 and a["licencia"] for a in manifiesto["archivos"])


def test_no_vuelve_a_bajar_lo_que_ya_existe(tmp_path):
    fuente = _fuente("md")
    (tmp_path / "grupo").mkdir()
    (tmp_path / "grupo" / fuente.nombre).write_bytes(MD)
    with _cliente({}) as cliente:                            # cualquier petición daría 404
        resultado, = df.descargar([fuente], tmp_path, cliente, avisar=lambda _: None)
    assert resultado.estado == "existente"


def test_un_error_no_detiene_las_demas_ni_deja_archivo(tmp_path):
    mala, buena = _fuente("pdf", "mala.pdf"), _fuente("pdf", "buena.pdf")
    with _cliente({buena.url: (200, PDF)}) as cliente:      # la mala da 404
        resultados = df.descargar([mala, buena], tmp_path, cliente, avisar=lambda _: None)
    assert [r.estado for r in resultados] == ["error", "descargado"]
    assert "404" in resultados[0].detalle
    assert not (tmp_path / "grupo" / "mala.pdf").exists()
