"""herramientas.py — lo que el modelo puede ejecutar (function calling).

    buscar_lineamiento   RAG en Chroma: top-8 -> umbral -> MMR -> 3, con su cita
    registrar_revision   guarda los hallazgos (solo los que tienen fuente válida)
    escalar_a_seguridad  deriva los puntos que requieren revisión humana
    exportar_informe     informe MD o CSV de la revisión

Igual que en el producto de la sesión 7:
- los ejecutores **nunca lanzan**: devuelven {"error": ...} y el modelo lo explica;
- la sesión **no** la elige el modelo: viene del turno. Una revisión de otra
  sesión no se puede leer, exportar ni escalar cambiando el id.
- las respuestas de registrar_revision le dicen al modelo qué hallazgos se
  rechazaron y por qué, para que los corrija en la siguiente vuelta.
"""
from __future__ import annotations

from typing import Any, Callable

from app import config
from app.datos import db, informes
from app.rag.indice import IndiceLineamientos
from app.seguridad.citas import normalizar_cita
from app.seguridad.entrada import enmascarar
from app.seguridad.salida import validar_hallazgos

SEVERIDAD = {"type": "string", "enum": ["alta", "media", "baja"]}

ESQUEMAS: list[dict[str, Any]] = [
    {
        "name": "buscar_lineamiento",
        "description": (
            "Busca en los lineamientos internos LSA-2026 (reglas POL-XX.N) y en OWASP ASVS 5.0, "
            "OWASP API Top 10 2023, OWASP Top 10:2025, OWASP Cheat Sheets y NIST. Devuelve "
            "fragmentos con su cita. Haz UNA búsqueda por riesgo y usa términos técnicos, por "
            "ejemplo 'BOLA validar que el recurso pertenece al usuario', 'SSRF URL enviada por el "
            "cliente', 'OTP límite de intentos fuerza bruta', 'idempotencia reintento abono core'. "
            "Puedes pedir varias búsquedas a la vez."),
        "parameters": {
            "type": "object",
            "properties": {
                "consulta": {"type": "string", "description": "Riesgo o control a buscar, con términos técnicos"},
                "alcance": {"type": "string", "enum": ["todos", "internos", "externos"],
                            "description": "internos = solo reglas POL; externos = OWASP/NIST. Por defecto: todos"},
            },
            "required": ["consulta"],
        },
    },
    {
        "name": "registrar_revision",
        "description": (
            "Registra la pre-revisión con TODOS los hallazgos sustentados. Cada hallazgo debe "
            "citar al menos una fuente devuelta por buscar_lineamiento: la regla POL-XX.N que "
            "incumple y, si aplica, la referencia OWASP (API1:2023, A01:2025, ASVS V8.2.1). "
            "Los hallazgos sin fuente válida se rechazan. Para corregir una revisión ya "
            "registrada en esta conversación, pasa su revision_id."),
        "parameters": {
            "type": "object",
            "properties": {
                "solucion": {"type": "string", "description": "Nombre corto de la solución revisada"},
                "resumen": {"type": "string", "description": "Dos o tres frases sobre el estado del diseño"},
                "revision_id": {"type": "string", "description": "Solo para actualizar una revisión existente"},
                "hallazgos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "titulo": {"type": "string"},
                            "severidad": SEVERIDAD,
                            "descripcion": {"type": "string", "description": "Qué está mal en ESTE diseño"},
                            "recomendacion": {"type": "string", "description": "Qué hacer, concreto"},
                            "fuentes": {"type": "array", "items": {"type": "string"},
                                        "description": "Citas: ['POL-01.1', 'API1:2023']"},
                            "requiere_seguridad": {"type": "boolean",
                                                   "description": "true si cae en una 'Revisión obligatoria' de LSA-2026"},
                            "nivel": {"type": "string", "enum": ["basico", "avanzado"],
                                      "description": "basico = se deduce de un componente o dato que la solicitud menciona; avanzado = requiere razonar sobre el flujo, el dinero, el negocio o terceros"},
                            "estado": {"type": "string", "enum": ["confirmado", "a_confirmar"],
                                       "description": "confirmado = la solicitud lo dice explícitamente (\"el login es propio\"); a_confirmar = se deduce del tipo de componente o dato y el equipo debe confirmarlo"},
                            "verificar": {"type": "string",
                                          "description": "Qué debe confirmar o responder el equipo (obligatorio si estado es a_confirmar)"},
                        },
                        "required": ["titulo", "severidad", "descripcion", "recomendacion",
                                     "fuentes", "requiere_seguridad", "nivel", "estado"],
                    },
                },
                "preguntas_pendientes": {"type": "array", "items": {"type": "string"},
                                         "description": "Lo que falta saber para completar la revisión"},
            },
            "required": ["solucion", "hallazgos"],
        },
    },
    {
        "name": "escalar_a_seguridad",
        "description": (
            "Deriva a Arquitectura de Seguridad los puntos de revisión obligatoria de una revisión "
            "registrada. No aprueba ni rechaza nada: deja constancia de qué debe revisar el área."),
        "parameters": {
            "type": "object",
            "properties": {
                "revision_id": {"type": "string"},
                "motivo": {"type": "string", "description": "Por qué requiere revisión humana"},
                "puntos": {"type": "array", "items": {"type": "string"},
                           "description": "Hallazgos o decisiones que debe revisar Seguridad"},
            },
            "required": ["revision_id", "motivo"],
        },
    },
    {
        "name": "exportar_informe",
        "description": "Genera el informe de una revisión registrada, en Markdown (md) o CSV.",
        "parameters": {
            "type": "object",
            "properties": {
                "revision_id": {"type": "string"},
                "formato": {"type": "string", "enum": ["md", "csv"]},
            },
            "required": ["revision_id"],
        },
    },
]

IDEMPOTENTES = {"buscar_lineamiento"}
MAX_HALLAZGOS = 40


def esquemas() -> list[dict[str, Any]]:
    return ESQUEMAS


class Herramientas:
    """Ejecutores de UN turno: se crea una instancia por turno (el índice se comparte),
    así dos conversaciones simultáneas no mezclan sus citas ni sus hallazgos."""

    def __init__(self, indice: IndiceLineamientos | None = None) -> None:
        self.indice = indice or IndiceLineamientos()
        self.citas_turno: list[str] = []
        self.documentos_turno: list[dict[str, Any]] = []
        self.revisiones_turno: list[str] = []
        self.hallazgos_turno: list[dict[str, Any]] = []
        self.rechazados_turno: list[dict[str, Any]] = []
        self.escalamientos_turno: list[str] = []
        self.informes_turno: list[dict[str, str]] = []

    def ejecutar(self, nombre: str, argumentos: dict[str, Any] | None, sesion_id: str) -> dict[str, Any]:
        funcion: Callable[..., dict[str, Any]] | None = getattr(self, f"_{nombre}", None)
        if nombre not in {e["name"] for e in ESQUEMAS} or funcion is None:
            return {"error": f"herramienta desconocida: {nombre}"}
        try:
            return funcion(sesion_id=sesion_id, **(argumentos or {}))
        except TypeError as exc:
            return {"error": f"argumentos inválidos para {nombre}: {exc}"}
        except Exception as exc:                       # noqa: BLE001 - nunca tumbar el turno
            return {"error": f"{type(exc).__name__}: {exc}"}

    # -- buscar_lineamiento -------------------------------------------------
    def _buscar_lineamiento(self, sesion_id: str, consulta: str, alcance: str = "todos",
                            **_: Any) -> dict[str, Any]:
        consulta, _tipos = enmascarar(str(consulta or "")[:500])
        if not consulta.strip():
            return {"error": "la consulta está vacía"}
        if not self.indice.disponible():
            return {"error": "no hay índice de lineamientos (ejecuta python -m scripts.indexar)",
                    "resultados": []}
        donde = None
        if alcance == "internos":
            donde = {"tipo": "lineamiento"}
        elif alcance == "externos":
            donde = {"tipo": {"$ne": "lineamiento"}}
        resultados = self.indice.buscar(consulta, donde=donde)
        salida = [r.to_dict() for r in resultados]
        for r in salida:
            self.citas_turno.append(normalizar_cita(r["cita"]))
        self.documentos_turno.extend(salida)
        if not salida:
            return {"resultados": [], "aviso": (
                f"Ningún lineamiento supera el umbral de similitud ({config.RAG_UMBRAL}). No "
                "cites una fuente que no hayas recuperado: reformula con otros términos técnicos "
                "o indica que no encontraste un lineamiento aplicable.")}
        return {"resultados": salida}

    # -- registrar_revision ---------------------------------------------------
    def _registrar_revision(self, sesion_id: str, solucion: str, hallazgos: list | None = None,
                            resumen: str = "", revision_id: str | None = None,
                            preguntas_pendientes: list | None = None, **_: Any) -> dict[str, Any]:
        crudos = list(hallazgos or [])[:MAX_HALLAZGOS]
        recuperadas = db.citas_de_sesion(sesion_id) | set(self.citas_turno)
        validos, rechazados = validar_hallazgos(crudos, recuperadas)
        solucion = enmascarar(str(solucion or "Solución sin nombre"))[0][:200]
        resumen = enmascarar(str(resumen or ""))[0][:2000]
        preguntas = [enmascarar(str(p))[0][:500] for p in (preguntas_pendientes or [])][:15]

        if revision_id:
            if not db.actualizar_revision(revision_id, sesion_id, resumen or None, preguntas or None,
                                          validos):
                return {"error": f"la revisión {revision_id} no existe en esta conversación"}
        else:
            revision_id = db.crear_revision(sesion_id, solucion, resumen, preguntas, validos)

        self.revisiones_turno.append(revision_id)
        self.hallazgos_turno = validos
        self.rechazados_turno = [{"titulo": r.titulo, "motivo": r.motivo,
                                  "fuentes_descartadas": r.fuentes} for r in rechazados]
        respuesta: dict[str, Any] = {
            "revision_id": revision_id,
            "hallazgos_registrados": len(validos),
            "requieren_seguridad": sum(h["requiere_seguridad"] for h in validos),
        }
        if rechazados:
            respuesta["rechazados"] = self.rechazados_turno
            respuesta["instruccion"] = ("Estos hallazgos no se registraron. Búscales una fuente "
                                        "con buscar_lineamiento y vuelve a registrar con el mismo "
                                        "revision_id, o descártalos.")
        sin_recuperar = [h["titulo"] for h in validos if h.get("fuente_recuperada") is False]
        if sin_recuperar:
            respuesta["aviso"] = ("Estos hallazgos citan fuentes que no recuperaste con "
                                  f"buscar_lineamiento: {sin_recuperar}. Verifícalas.")
        return respuesta

    # -- escalar_a_seguridad -------------------------------------------------
    def _escalar_a_seguridad(self, sesion_id: str, revision_id: str, motivo: str,
                             puntos: list | None = None, **_: Any) -> dict[str, Any]:
        motivo = enmascarar(str(motivo or ""))[0][:1000]
        puntos_limpios = [enmascarar(str(p))[0][:500] for p in (puntos or [])][:30]
        escalamiento = db.escalar(revision_id, sesion_id, motivo, puntos_limpios)
        if escalamiento is None:
            return {"error": f"la revisión {revision_id} no existe en esta conversación"}
        self.escalamientos_turno.append(escalamiento)
        return {"escalamiento_id": escalamiento, "revision_id": revision_id,
                "estado": "derivado a Arquitectura de Seguridad",
                "nota": "Queda registrado para el área. No es una aprobación ni un rechazo."}

    # -- exportar_informe ----------------------------------------------------
    def _exportar_informe(self, sesion_id: str, revision_id: str, formato: str = "md",
                          **_: Any) -> dict[str, Any]:
        formato = (formato or "md").lower()
        if formato not in informes.FORMATOS:
            return {"error": f"formato no soportado: {formato} (md o csv)"}
        revision = db.obtener_revision(revision_id, sesion_id)
        if revision is None:
            return {"error": f"la revisión {revision_id} no existe en esta conversación"}
        ruta = informes.exportar(revision, formato)
        info = {"revision_id": revision_id, "formato": formato,
                "url": f"/api/revisiones/{revision_id}/informe.{formato}", "archivo": ruta.name}
        self.informes_turno.append(info)
        return info


__all__ = ["ESQUEMAS", "Herramientas", "IDEMPOTENTES", "esquemas"]
