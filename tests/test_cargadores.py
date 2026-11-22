"""Pruebas de app/rag/cargadores.py: limpieza y estructura, sin red ni modelos."""
from __future__ import annotations

import zipfile
from collections import Counter
from pathlib import Path

from app import config
from app.rag import cargadores as c

RUTA_POLITICAS = config.CARPETA_LINEAMIENTOS / "politicas_seguridad_banco_andesur.md"


def test_lineamientos_reales_dan_73_reglas_de_15_politicas():
    doc = c.cargar(RUTA_POLITICAS)
    assert doc.tipo == "lineamiento" and doc.fuente == "LSA-2026"
    assert len(doc.bloques) == 73
    assert len({b.extra["politica"] for b in doc.bloques}) == 15
    primera = doc.bloques[0]
    assert primera.cita == "POL-01.1" and primera.atomico
    assert "cuentas, tarjetas" in primera.extra["revision_obligatoria"]
    assert "API1:2023" in primera.extra["referencias"]
    assert doc.bloques[-1].cita == "POL-15.4"


def test_csv_de_asvs_un_bloque_por_requisito(tmp_path):
    ruta = tmp_path / "asvs.csv"
    ruta.write_text(
        "chapter_id,chapter_name,section_id,section_name,req_id,req_description,L\n"
        'V8,Authorization,V8.2,General Authorization Design,V8.2.1,"Verify that access '
        'is restricted to the owner, at the server.",1\n'
        "V8,Authorization,V8.2,General Authorization Design,V8.2.2,,1\n",   # vacío: fuera
        encoding="utf-8")
    doc = c.cargar(ruta)
    assert doc.tipo == "asvs" and len(doc.bloques) == 1
    b = doc.bloques[0]
    assert b.cita == "ASVS V8.2.1" and b.atomico and b.extra["nivel_asvs"] == "1"
    assert "V8 Authorization > V8.2" in b.seccion


def test_csv_generico_fila_a_texto(tmp_path):
    ruta = tmp_path / "controles.csv"
    ruta.write_text("id,control\nC1,Cifrar en tránsito\n", encoding="utf-8")
    doc = c.cargar(ruta)
    assert doc.tipo == "otro"
    assert doc.bloques[0].texto == "id: C1; control: Cifrar en tránsito"


def test_markdown_quita_imagenes_enlaces_tablas_y_secciones_de_ruido(tmp_path):
    ruta = tmp_path / "A01_2025-Broken_Access_Control.md"
    ruta.write_text(
        "# A01:2025 Pérdida de Control de Acceso ![icono](x.png){: style=\"a\"}\n\n"
        "## Tabla de puntuación\n\n<table><tr><td>40</td></tr></table>\n\n"
        "## Cómo prevenir\n\nValidar en el **servidor**, ver [la guía](https://x.org).\n\n"
        "| Riesgo | Control |\n| - | - |\n| BOLA | Validar dueño |\n\n"
        "## Referencias\n\n- [OWASP](https://owasp.org)\n", encoding="utf-8")
    doc = c.cargar(ruta)
    assert doc.fuente == "A01:2025" and doc.tipo == "owasp"
    assert len(doc.bloques) == 1
    texto = doc.bloques[0].texto
    assert texto.startswith("Validar en el servidor, ver la guía.")
    assert "BOLA ; Validar dueño" in texto and "https" not in texto and "|" not in texto
    assert doc.bloques[0].seccion.endswith("> Cómo prevenir")
    assert doc.limpieza["imagenes"] == 1 and doc.limpieza["secciones_omitidas"] == 2


def test_cita_base_por_tipo_de_fuente():
    assert c.cita_base(Path("NIST.SP.800-63b-4.pdf"), "")[0] == "NIST SP 800-63B-4"
    assert c.cita_base(Path("x.md"), "API3:2023 Broken Object Property")[0] == "API3:2023"
    assert c.cita_base(Path("Logging_Cheat_Sheet.md"), "")[0] == "OWASP Cheat Sheet: Logging"
    assert c.cita_base(Path("guia_interna.md"), "Guía") == ("guia interna", "otro")


def test_pdf_quita_encabezados_pies_numeros_e_indice():
    cuerpos = [
        ["Subjects are authenticated before every session.", "Trust is never implicit.",
         "Assets are inventoried continuously.", "Access is granted per request."],
        ["Policy engines decide on each access.", "Enforcement points apply them.",
         "Telemetry informs the decision.", "Devices report their posture."],
        ["Zero trust is a paradigm focused on resource protection and end-to-",
         "end security of every request.", "Networks are assumed hostile.",
         "Segmentation limits lateral movement."],
        ["Credentials expire quickly.", "Keys rotate on a schedule.",
         "Secrets live in a vault.", "Audits review every grant."],
        ["Workloads use mutual TLS.", "Service identities are short lived.",
         "Gateways validate tokens.", "Errors never leak details."],
        ["Monitoring feeds the SIEM.", "Alerts reach the response team.",
         "Incidents are rehearsed.", "Lessons update the policies."],
    ]
    paginas = []
    for n, cuerpo in enumerate(cuerpos, 1):
        titulo = ("1 Introduction ........................ 4" if n == 1
                  else "2 Zero Trust Basics" if n == 2 else "")
        paginas.append("\n".join(
            ["NIST SP 800-207 ZERO TRUST ARCHITECTURE",
             "This publication is available free of charge from: https://doi.org/x",
             titulo, *cuerpo, str(n)]))
    limpieza: Counter = Counter()
    bloques = c.limpiar_paginas_pdf(paginas, limpieza)
    unido = " ".join(b.texto for b in bloques)
    assert "ZERO TRUST ARCHITECTURE" not in unido and "doi.org" not in unido
    assert "end-to-end security" in unido
    assert "Lessons update the policies." in unido            # el cuerpo se conserva
    # 2 encabezados x 6 páginas + 6 números de página (que, al repetirse en el pie,
    # se cuentan como pie); la línea de índice aparte
    assert limpieza["encabezados_pies"] + limpieza["numeros_pagina"] == 18
    assert limpieza["lineas_indice"] == 1
    assert not any(b.texto.strip().isdigit() for b in bloques)
    assert any(b.seccion == "2 Zero Trust Basics" and b.pagina == 2 for b in bloques)


def test_html_y_docx_pasan_por_markdown(tmp_path):
    html = tmp_path / "REST_Security_Cheat_Sheet.html"
    html.write_text("<html><nav>menú</nav><article><h1>REST Security Cheat Sheet</h1>"
                    "<h2>HTTPS</h2><p>Secure REST services must only provide HTTPS "
                    "endpoints.</p><script>x()</script></article></html>", encoding="utf-8")
    doc = c.cargar(html)
    assert doc.fuente == "OWASP Cheat Sheet: REST Security"
    assert doc.bloques[0].texto == "Secure REST services must only provide HTTPS endpoints."
    assert "menú" not in doc.bloques[0].texto

    docx = tmp_path / "guia.docx"
    xml = ('<w:document><w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
           "<w:r><w:t>Guía interna</w:t></w:r></w:p><w:p><w:r><w:t>Usar TLS 1.2 o "
           "superior.</w:t></w:r></w:p></w:body></w:document>")
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml", xml)
    doc = c.cargar(docx)
    assert doc.titulo == "Guía interna" and doc.bloques[0].texto == "Usar TLS 1.2 o superior."


def test_un_documento_en_varios_formatos_se_indexa_una_vez(tmp_path):
    for nombre in ("ASVS_5.0.0_en.csv", "ASVS_5.0.0_en.pdf", "ASVS_5.0.0_en.docx",
                   "guia.pdf", "notas.xlsx"):
        (tmp_path / nombre).write_bytes(b"x")
    elegidas = [p.name for p in c.archivos_de(tmp_path)]
    assert elegidas == ["ASVS_5.0.0_en.csv", "guia.pdf"]       # xlsx: sin cargador


def test_formato_no_soportado(tmp_path):
    ruta = tmp_path / "planilla.xlsx"
    ruta.write_bytes(b"x")
    try:
        c.cargar(ruta)
    except c.FormatoNoSoportado as exc:
        assert ".xlsx" in str(exc)
    else:
        raise AssertionError("debió rechazar .xlsx")
