"""salida.py — guardias que revisan lo que dice el modelo antes de mostrarlo.

    respuesta del modelo
      ├─ 1. datos sensibles     se vuelven a enmascarar (no deberían aparecer)
      ├─ 2. aprobaciones        "apruebo", "puede pasar a producción"... se retiran
      ├─ 3. hallazgos           cada uno con al menos una fuente válida (POL que exista,
      │                         API1-10:2023, A01-A10:2025, ASVS, NIST...); los que no la
      │                         tienen no se entregan. Severidad alta -> va a Seguridad.
      └─ 4. aviso               "no reemplaza la revisión de Arquitectura de Seguridad"

Las negaciones no se tocan: "no puede pasar a producción sin la revisión de
Seguridad" es justo lo que el bot tiene que decir.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app import config
from app.seguridad.citas import es_cita_valida, normalizar_cita
from app.seguridad.entrada import enmascarar, normalizar

# ---------------------------------------------------------------------------
# Aprobaciones
# ---------------------------------------------------------------------------
PATRONES_APROBACION = [
    r"\b(apruebo|aprobamos|lo apruebo|la apruebo|se aprueba|queda aprobad[oa]s?|esta aprobad[oa]s?|"
    r"estan aprobad[oa]s|doy (el |mi )?(visto bueno|conformidad|ok)|tienes (el |mi )?(visto bueno|ok))\b",
    r"\b(puede|pueden|podra|podran|puedes|pueden ya|ya puede|ya pueden)\b.{0,15}\b(pasar|salir|ir|desplegarse|subir)\b.{0,6}\ba produccion\b",
    r"\b(esta|estan|queda|quedan)\b.{0,10}\blist[oa]s? para (pasar a )?produccion\b",
    r"\b(no (requiere|necesita|hace falta)|sin necesidad de)\b.{0,10}\b(revision|aprobacion)\b.{0,20}\bseguridad\b",
    r"\b(approved|ready for production|good to go|cleared for release)\b",
]
_APROBACION = [re.compile(p) for p in PATRONES_APROBACION]
# Una negación o condición antes de la frase la vuelve correcta.
RE_NEGACION = re.compile(r"\b(no|ni|nunca|jamas|sin|hasta que|antes de|antes que|not|never)\b")
RE_CONDICION = re.compile(r"\b(una vez que|cuando|despues de que|solo si|si seguridad|si el area|"
                          r"tras la (revision|aprobacion))\b")
RE_ORACION = re.compile(r"(?<=[.!?])\s+|\n+")

AVISO_RETIRADO = ("(Se retiró una frase que sonaba a aprobación: la decisión de pasar a "
                  "producción es del área de Arquitectura de Seguridad.)")


def _es_aprobacion(oracion: str) -> bool:
    normalizado = " ".join(normalizar(oracion).split())
    for patron in _APROBACION:
        m = patron.search(normalizado)
        if not m:
            continue
        # "no requiere revisión de Seguridad" ya incluye la negación en el patrón
        if patron is _APROBACION[3]:
            return True
        antes = normalizado[max(0, m.start() - 30):m.start()]
        if RE_NEGACION.search(antes) or RE_CONDICION.search(normalizado):
            continue
        return True
    return False


def quitar_aprobaciones(texto: str) -> tuple[str, list[str]]:
    """Retira las oraciones que aprueban. Devuelve (texto, oraciones retiradas)."""
    if not texto:
        return texto, []
    retiradas: list[str] = []
    partes = re.split(r"((?<=[.!?])\s+|\n+)", texto)
    salida: list[str] = []
    for parte in partes:
        if parte and not RE_ORACION.fullmatch(parte) and _es_aprobacion(parte):
            retiradas.append(parte.strip())
            continue
        salida.append(parte)
    limpio = re.sub(r"[ \t]{2,}", " ", "".join(salida))
    limpio = re.sub(r"[ \t]+\n", "\n", limpio).strip()
    if retiradas:
        limpio = f"{limpio}\n\n{AVISO_RETIRADO}".strip()
    return limpio, retiradas


# ---------------------------------------------------------------------------
# Hallazgos
# ---------------------------------------------------------------------------
SEVERIDADES = {
    "alta": "alta", "high": "alta", "critica": "alta", "crítica": "alta", "critical": "alta",
    "media": "media", "medium": "media", "moderada": "media",
    "baja": "baja", "low": "baja", "informativa": "baja",
}
NIVELES = {"basico": "basico", "básico": "basico", "avanzado": "avanzado",
           "b": "basico", "a": "avanzado"}


def _como_lista(valor: Any) -> list[str]:
    if valor is None:
        return []
    if isinstance(valor, str):
        return [v.strip() for v in re.split(r"[,;\n]", valor) if v.strip()]
    if isinstance(valor, Iterable):
        return [str(v).strip() for v in valor if str(v).strip()]
    return [str(valor)]


def _texto_seguro(valor: Any, limite: int = 2000) -> str:
    texto, _ = enmascarar(str(valor or "").strip())
    texto, _ = quitar_aprobaciones(texto)
    return texto.replace(AVISO_RETIRADO, "").strip()[:limite]


def _a_bool(valor: Any) -> bool:
    if isinstance(valor, str):
        return valor.strip().lower() in {"true", "si", "sí", "yes", "1"}
    return bool(valor)


@dataclass
class HallazgoRechazado:
    titulo: str
    motivo: str
    fuentes: list[str] = field(default_factory=list)


def validar_hallazgo(crudo: dict[str, Any],
                     citas_recuperadas: set[str] | None = None) -> dict[str, Any] | HallazgoRechazado:
    """Normaliza un hallazgo del modelo o lo rechaza con su motivo."""
    titulo = _texto_seguro(crudo.get("titulo"), 200)
    if not titulo:
        return HallazgoRechazado("(sin título)", "falta el título")
    validas: list[str] = []
    descartadas: list[str] = []
    for fuente in _como_lista(crudo.get("fuentes")):
        ok, motivo = es_cita_valida(fuente)
        (validas if ok else descartadas).append(normalizar_cita(fuente) if ok else f"{fuente} ({motivo})")
    validas = list(dict.fromkeys(validas))
    if not validas:
        return HallazgoRechazado(titulo, "sin fuente válida", descartadas)

    severidad = SEVERIDADES.get(str(crudo.get("severidad", "")).strip().lower(), "media")
    requiere = _a_bool(crudo.get("requiere_seguridad")) or severidad == "alta"
    hallazgo = {
        "titulo": titulo,
        "severidad": severidad,
        "descripcion": _texto_seguro(crudo.get("descripcion")),
        "recomendacion": _texto_seguro(crudo.get("recomendacion")),
        "fuentes": validas,
        "requiere_seguridad": requiere,
        "nivel": NIVELES.get(str(crudo.get("nivel", "")).strip().lower(), "basico"),
        "cita_interna": any(f.startswith("POL-") for f in validas),
    }
    if citas_recuperadas is not None:
        hallazgo["fuente_recuperada"] = any(f in citas_recuperadas for f in validas)
    if descartadas:
        hallazgo["fuentes_descartadas"] = descartadas
    return hallazgo


ORDEN_SEVERIDAD = {"alta": 0, "media": 1, "baja": 2}


def validar_hallazgos(crudos: Iterable[dict[str, Any]], citas_recuperadas: set[str] | None = None
                      ) -> tuple[list[dict[str, Any]], list[HallazgoRechazado]]:
    validos: list[dict[str, Any]] = []
    rechazados: list[HallazgoRechazado] = []
    for crudo in crudos or []:
        if not isinstance(crudo, dict):
            rechazados.append(HallazgoRechazado(str(crudo)[:80], "formato inválido"))
            continue
        r = validar_hallazgo(crudo, citas_recuperadas)
        (rechazados if isinstance(r, HallazgoRechazado) else validos).append(r)
    validos.sort(key=lambda h: (ORDEN_SEVERIDAD[h["severidad"]], not h["requiere_seguridad"]))
    return validos, rechazados


# ---------------------------------------------------------------------------
# Decisión
# ---------------------------------------------------------------------------
@dataclass
class DecisionSalida:
    texto: str
    hallazgos: list[dict[str, Any]] = field(default_factory=list)
    rechazados: list[HallazgoRechazado] = field(default_factory=list)
    aprobaciones_retiradas: list[str] = field(default_factory=list)
    enmascarados: list[str] = field(default_factory=list)

    @property
    def modificada(self) -> bool:
        return bool(self.rechazados or self.aprobaciones_retiradas or self.enmascarados)


def con_aviso(texto: str) -> str:
    """Agrega el aviso obligatorio una sola vez."""
    if config.AVISO_SEGURIDAD in texto:
        return texto
    return f"{texto.rstrip()}\n\n{config.AVISO_SEGURIDAD}".strip()


def revisar_salida(texto: str, hallazgos: Iterable[dict[str, Any]] | None = None,
                   citas_recuperadas: Iterable[str] | None = None,
                   aviso: bool = True) -> DecisionSalida:
    recuperadas = ({normalizar_cita(c) for c in citas_recuperadas}
                   if citas_recuperadas is not None else None)
    limpio, tipos = enmascarar(texto or "")
    limpio, retiradas = quitar_aprobaciones(limpio)
    validos, rechazados = validar_hallazgos(hallazgos or [], recuperadas)
    if rechazados:
        nombres = "; ".join(f"«{r.titulo}» ({r.motivo})" for r in rechazados)
        limpio += (f"\n\n(No se incluyeron {len(rechazados)} observación(es) sin una fuente "
                   f"verificable: {nombres}.)")
    if aviso:
        limpio = con_aviso(limpio)
    return DecisionSalida(limpio, validos, rechazados, retiradas, tipos)


__all__ = ["AVISO_RETIRADO", "DecisionSalida", "HallazgoRechazado", "con_aviso",
           "quitar_aprobaciones", "revisar_salida", "validar_hallazgo", "validar_hallazgos"]
