"""chat.py — el turno completo (adaptado de chat_service.py de la sesión 7).

    mensaje
      ├─ 0. validación              vacío o demasiado largo -> error claro (422)
      ├─ 1. guardias de entrada     enmascarar · proyecto confidencial / tema estratégico /
      │                             inyección -> respuesta fija, sin modelo y sin guardar el texto
      ├─ 2. alcance                 "apruébame el pase", "revisa este código", "mi saldo"
      │                             -> respuesta fija, sin modelo
      ├─ 3. FAQ del proceso         similitud alta -> respuesta fija, sin modelo
      ├─ 4. modelo + herramientas   buscar_lineamiento · registrar_revision ·
      │                             escalar_a_seguridad · exportar_informe
      │     └─ sin modelo (caído o sin clave) -> modo demo: lineamientos más cercanos
      ├─ 5. guardias de salida      sin aprobaciones, sin datos sensibles, con aviso
      └─ 6. cierre                  SQLite (mensajes redactados, turno, citas) + log JSONL

El orden importa: lo que se puede decidir sin modelo se decide antes, y lo que
no debe salir del banco (un proyecto confidencial) nunca llega al proveedor.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from app import config
from app.datos import db, logs
from app.llm import Router, SinProveedores
from app.rag.indice import IndiceLineamientos
from app.seguridad import alcance as alcance_mod
from app.seguridad.entrada import revisar_entrada
from app.seguridad.salida import con_aviso, revisar_salida
from app.servicios import prompts
from app.servicios.faq import ClasificadorFAQ
from app.servicios.herramientas import IDEMPOTENTES, Herramientas, esquemas


class ErrorEntrada(ValueError):
    """El mensaje no se puede procesar tal como llegó (vacío, muy largo)."""


@dataclass
class RespuestaChat:
    respuesta: str
    sesion_id: str
    usuario_id: str
    turno_id: str
    ruta: str                       # filtro | fuera_de_alcance | faq | modelo | demo
    motivo: str = ""
    fuente: str = ""
    faq_id: str | None = None
    similitud: float | None = None
    revision_id: str | None = None
    hallazgos: list[dict[str, Any]] = field(default_factory=list)
    rechazados: list[dict[str, Any]] = field(default_factory=list)
    escalamientos: list[str] = field(default_factory=list)
    informes: list[dict[str, str]] = field(default_factory=list)
    documentos: list[dict[str, Any]] = field(default_factory=list)
    herramientas: list[dict[str, Any]] = field(default_factory=list)
    enmascarados: dict[str, int] = field(default_factory=dict)
    modo: str = "real"              # real | sin_llm | demo
    modelo: str = "—"
    latencia_ms: int = 0
    tokens: list[int] = field(default_factory=lambda: [0, 0])
    aviso: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ServicioChat:
    """Un turno = guardias + alcance + FAQ + (modelo con herramientas) + salida + cierre."""

    def __init__(self, router: Router | None = None, indice: IndiceLineamientos | None = None,
                 faq: ClasificadorFAQ | None = None) -> None:
        db.inicializar()
        self.router = router or Router()
        self.indice = indice or IndiceLineamientos()
        self.faq = faq or ClasificadorFAQ()
        self.sistema = prompts.sistema()

    # -- memoria -----------------------------------------------------------
    def historial(self, sesion_id: str) -> list[dict[str, str]]:
        return [{"role": m["rol"], "content": m["contenido"]}
                for m in db.mensajes_de(sesion_id, limite=config.MAX_MENSAJES)]

    def reiniciar(self, sesion_id: str) -> int:
        return db.borrar_sesion(sesion_id)

    # -- turno -------------------------------------------------------------
    def responder(self, mensaje: str, usuario_id: str = "lider-tecnico",
                  sesion_id: str = "demo") -> RespuestaChat:
        mensaje = (mensaje or "").strip()
        if not mensaje:
            raise ErrorEntrada("La descripción está vacía. Cuéntame cómo está armada tu solución: "
                               "componentes, endpoints, autenticación, datos e integraciones.")
        if len(mensaje) > config.LIMITE_MENSAJE:
            raise ErrorEntrada(f"El mensaje supera los {config.LIMITE_MENSAJE} caracteres. "
                               "Envíalo en partes (por ejemplo, un módulo por mensaje).")

        inicio = time.perf_counter()
        turno_id = logs.nuevo_turno()
        db.asegurar_sesion(sesion_id, usuario_id)
        logs.info("turno.inicio", turno_id, sesion=sesion_id, usuario=usuario_id,
                  largo=len(mensaje))

        # 1. guardias de entrada (el texto original no sigue más allá de aquí)
        entrada = revisar_entrada(mensaje)
        del mensaje
        enmascarados = entrada.resumen_enmascarado
        if enmascarados:
            logs.info("dato.enmascarado", turno_id, tipos=",".join(sorted(enmascarados)),
                      cantidad=sum(enmascarados.values()))
        if not entrada.permitido:
            evento = "guardia.inyeccion" if entrada.motivo == "inyeccion" else "guardia.bloqueo"
            logs.aviso(evento, turno_id, motivo=entrada.motivo)
            return self._cerrar(con_aviso(entrada.respuesta), entrada.guardar_como, sesion_id,
                                usuario_id, turno_id, inicio, ruta="filtro", motivo=entrada.motivo,
                                fuente="guardia de entrada", modo="sin_llm",
                                enmascarados=enmascarados)
        texto = entrada.texto

        # 2. fuera de alcance
        if fuera := alcance_mod.detectar_fuera_de_alcance(texto):
            logs.info("alcance.fuera", turno_id, categoria=fuera.categoria)
            return self._cerrar(con_aviso(fuera.respuesta), texto, sesion_id, usuario_id,
                                turno_id, inicio, ruta="fuera_de_alcance", motivo=fuera.categoria,
                                fuente="guardia de alcance", modo="sin_llm",
                                enmascarados=enmascarados)

        # 3. FAQ del proceso
        decision = self.faq.clasificar(texto)
        logs.info("clasificacion", turno_id, ruta=decision.ruta, sim=round(decision.similitud, 3),
                  segunda=round(decision.similitud_segunda, 3),
                  faq=decision.faq.id if decision.faq else "-")
        if decision.ruta == "faq" and decision.faq is not None:
            logs.info("faq.hit", turno_id, faq=decision.faq.id, sim=round(decision.similitud, 3))
            return self._cerrar(con_aviso(decision.faq.respuesta), texto, sesion_id, usuario_id,
                                turno_id, inicio, ruta="faq", fuente=f"FAQ ({decision.faq.id})",
                                faq_id=decision.faq.id, similitud=round(decision.similitud, 4),
                                modo="sin_llm", enmascarados=enmascarados)

        # 4. modelo con herramientas
        return self._con_modelo(texto, sesion_id, usuario_id, turno_id, inicio, enmascarados)

    # -- camino con modelo -------------------------------------------------
    def _con_modelo(self, texto: str, sesion_id: str, usuario_id: str, turno_id: str,
                    inicio: float, enmascarados: dict[str, int]) -> RespuestaChat:
        herramientas = Herramientas(self.indice)
        mensajes: list[dict[str, Any]] = ([{"role": "system", "content": self.sistema}]
                                          + self.historial(sesion_id)
                                          + [{"role": "user", "content": texto}])
        usadas: list[dict[str, Any]] = []
        cache: dict[str, dict[str, Any]] = {}
        tokens = [0, 0]
        modelo = "—"
        try:
            for vuelta in range(config.MAX_VUELTAS + 1):
                ultima = vuelta == config.MAX_VUELTAS
                resp = self.router.chat(mensajes, tools=None if ultima else esquemas(),
                                        temperature=config.LLM_TEMPERATURA,
                                        max_tokens=config.LLM_MAX_TOKENS)
                modelo = resp.modelo or modelo
                tokens[0] += int(resp.uso.get("prompt_tokens", 0) or 0)
                tokens[1] += int(resp.uso.get("completion_tokens", 0) or 0)
                ms = int((resp.latencia or 0) * 1000)
                logs.info("llm.llamada", turno_id, modelo=resp.modelo, ms=ms, vuelta=vuelta,
                          tool_calls=len(resp.tool_calls or []))
                if ms > config.LLM_LENTO_MS:
                    logs.aviso("llm.lento", turno_id, ms=ms, umbral=config.LLM_LENTO_MS)
                if not resp.tool_calls or ultima:
                    return self._final(resp.texto or "", herramientas, usadas, sesion_id,
                                       usuario_id, turno_id, inicio, texto, enmascarados,
                                       modelo, tokens)

                mensajes.append({"role": "assistant", "content": resp.texto or "",
                                 "tool_calls": [{"id": tc["id"], "type": "function",
                                                 "function": {"name": tc["name"],
                                                              "arguments": json.dumps(
                                                                  tc["arguments"] or {},
                                                                  ensure_ascii=False)}}
                                                for tc in resp.tool_calls]})
                for tc in resp.tool_calls:
                    resultado = self._ejecutar(tc, herramientas, cache, usadas, sesion_id, turno_id)
                    mensajes.append({"role": "tool", "tool_call_id": tc["id"], "name": tc["name"],
                                     "content": json.dumps(resultado, ensure_ascii=False,
                                                           default=str)})
        except SinProveedores as exc:
            aviso = str(exc).splitlines()[0]
            logs.aviso("degradacion", turno_id, motivo=aviso)
            return self._demo(texto, herramientas, usadas, sesion_id, usuario_id, turno_id,
                              inicio, enmascarados, aviso, tokens)
        raise AssertionError("inalcanzable")                    # pragma: no cover

    def _ejecutar(self, tc: dict[str, Any], herramientas: Herramientas,
                  cache: dict[str, dict[str, Any]], usadas: list[dict[str, Any]],
                  sesion_id: str, turno_id: str) -> dict[str, Any]:
        nombre, argumentos = tc["name"], tc.get("arguments") or {}
        firma = nombre + json.dumps(argumentos, sort_keys=True, ensure_ascii=False)
        if nombre in IDEMPOTENTES and firma in cache:
            return cache[firma]
        t0 = time.perf_counter()
        resultado = herramientas.ejecutar(nombre, argumentos, sesion_id)
        ms = int((time.perf_counter() - t0) * 1000)
        if nombre in IDEMPOTENTES:
            cache[firma] = resultado
        resumen = self._resumen(nombre, resultado)
        usadas.append({"nombre": nombre, "ok": "error" not in resultado, "ms": ms,
                       "resumen": resumen,
                       "consulta": argumentos.get("consulta") if nombre == "buscar_lineamiento" else None})
        logs.info("herramienta.ejecutada", turno_id, herramienta=nombre,
                  ok="error" not in resultado, ms=ms, resumen=resumen)
        if nombre == "registrar_revision" and resultado.get("revision_id"):
            logs.info("revision.registrada", turno_id, revision=resultado["revision_id"],
                      hallazgos=resultado.get("hallazgos_registrados"),
                      seguridad=resultado.get("requieren_seguridad"))
            for r in resultado.get("rechazados", []):
                logs.aviso("hallazgo.rechazado", turno_id, titulo=r["titulo"][:60], motivo=r["motivo"])
        if nombre == "escalar_a_seguridad" and resultado.get("escalamiento_id"):
            logs.info("escalado", turno_id, escalamiento=resultado["escalamiento_id"])
        return resultado

    @staticmethod
    def _resumen(nombre: str, resultado: dict[str, Any]) -> str:
        if "error" in resultado:
            return f"error: {str(resultado['error'])[:80]}"
        if nombre == "buscar_lineamiento":
            r = resultado.get("resultados", [])
            return f"{len(r)} fragmentos: " + ", ".join(x["cita"] for x in r) if r else "sin resultados"
        if nombre == "registrar_revision":
            return (f"{resultado['revision_id']}: {resultado['hallazgos_registrados']} hallazgos, "
                    f"{len(resultado.get('rechazados', []))} rechazados")
        if nombre == "escalar_a_seguridad":
            return resultado["escalamiento_id"]
        if nombre == "exportar_informe":
            return resultado["url"]
        return ""

    def _final(self, texto_modelo: str, herramientas: Herramientas, usadas: list[dict[str, Any]],
               sesion_id: str, usuario_id: str, turno_id: str, inicio: float, texto: str,
               enmascarados: dict[str, int], modelo: str, tokens: list[int]) -> RespuestaChat:
        texto_modelo = texto_modelo.strip()
        if not texto_modelo and herramientas.revisiones_turno:
            h = herramientas.hallazgos_turno
            texto_modelo = (f"Registré la revisión {herramientas.revisiones_turno[-1]} con "
                            f"{len(h)} hallazgos ({sum(x['requiere_seguridad'] for x in h)} van a "
                            "Seguridad).")
        salida = revisar_salida(texto_modelo or "No pude completar la revisión. Intenta de nuevo.")
        if salida.aprobaciones_retiradas:
            logs.aviso("salida.aprobacion_retirada", turno_id,
                       cantidad=len(salida.aprobaciones_retiradas))
        if salida.enmascarados:
            logs.aviso("salida.enmascarada", turno_id, tipos=",".join(sorted(set(salida.enmascarados))))
        return self._cerrar(salida.texto, texto, sesion_id, usuario_id, turno_id, inicio,
                            ruta="modelo", fuente=self._fuente(usadas), modo="real", modelo=modelo,
                            tokens=tokens, herramientas_turno=herramientas, usadas=usadas,
                            enmascarados=enmascarados)

    # -- modo demo: sin modelo el producto sigue siendo útil -----------------
    def _demo(self, texto: str, herramientas: Herramientas, usadas: list[dict[str, Any]],
              sesion_id: str, usuario_id: str, turno_id: str, inicio: float,
              enmascarados: dict[str, int], aviso: str, tokens: list[int]) -> RespuestaChat:
        """Sin modelo no se redactan hallazgos: se muestran los lineamientos más cercanos."""
        resultado = herramientas.ejecutar("buscar_lineamiento", {"consulta": texto[:1500]}, sesion_id)
        documentos = resultado.get("resultados", [])
        if documentos:
            lineas = []
            for d in documentos:
                cuerpo_doc = d["texto"]
                if cuerpo_doc.startswith(d["cita"]):          # las reglas POL empiezan por su código
                    cuerpo_doc = cuerpo_doc[len(d["cita"]):].lstrip(" :")
                lineas.append(f"- [{d['cita']}] {cuerpo_doc[:260]}{'…' if len(cuerpo_doc) > 260 else ''}")
            cuerpo = ("Estoy en modo demo: no hay un modelo disponible, así que no puedo redactar "
                      "hallazgos. Estos son los lineamientos más relacionados con tu descripción:\n"
                      + "\n".join(lineas)
                      + "\n\nConfigura LLM_BASE_URL, LLM_API_KEY y LLM_MODEL en el .env (o revisa "
                        "la conexión) para obtener la pre-revisión completa.")
        else:
            cuerpo = ("Estoy en modo demo: no hay un modelo disponible y no encontré lineamientos "
                      "cercanos a tu mensaje. Configura LLM_BASE_URL, LLM_API_KEY y LLM_MODEL en "
                      "el .env (o revisa la conexión).")
        return self._cerrar(con_aviso(cuerpo), texto, sesion_id, usuario_id, turno_id, inicio,
                            ruta="demo", fuente="modo demo · lineamientos", modo="demo",
                            tokens=tokens, herramientas_turno=herramientas, usadas=usadas,
                            enmascarados=enmascarados, aviso=aviso)

    # -- cierre ------------------------------------------------------------
    @staticmethod
    def _fuente(usadas: list[dict[str, Any]]) -> str:
        nombres = list(dict.fromkeys(u["nombre"] for u in usadas))
        return "LLM + " + ", ".join(nombres) if nombres else "LLM (sin herramientas)"

    def _cerrar(self, respuesta: str, guardar: str, sesion_id: str, usuario_id: str,
                turno_id: str, inicio: float, *, ruta: str, motivo: str = "", fuente: str = "",
                faq_id: str | None = None, similitud: float | None = None, modo: str = "real",
                modelo: str = "—", tokens: list[int] | None = None,
                herramientas_turno: Herramientas | None = None,
                usadas: list[dict[str, Any]] | None = None,
                enmascarados: dict[str, int] | None = None, aviso: str | None = None) -> RespuestaChat:
        latencia = int((time.perf_counter() - inicio) * 1000)
        tokens = list(tokens or [0, 0])
        h = herramientas_turno
        citas = list(dict.fromkeys(h.citas_turno)) if h else []
        db.guardar_mensaje(sesion_id, turno_id, "user", guardar)
        db.guardar_mensaje(sesion_id, turno_id, "assistant", respuesta)
        db.guardar_turno(turno_id, sesion_id, ruta, fuente=fuente, motivo=motivo, faq_id=faq_id,
                         similitud=similitud, modelo=modelo, tokens=(tokens[0], tokens[1]),
                         latencia_ms=latencia, herramientas=usadas or [], citas=citas,
                         enmascarados=enmascarados or {})
        logs.info("turno.fin", turno_id, ruta=ruta, ms=latencia, tokens_entrada=tokens[0],
                  tokens_salida=tokens[1], herramientas=len(usadas or []),
                  hallazgos=len(h.hallazgos_turno) if h else 0)
        return RespuestaChat(
            respuesta=respuesta, sesion_id=sesion_id, usuario_id=usuario_id, turno_id=turno_id,
            ruta=ruta, motivo=motivo, fuente=fuente, faq_id=faq_id, similitud=similitud,
            revision_id=(h.revisiones_turno[-1] if h and h.revisiones_turno else None),
            hallazgos=h.hallazgos_turno if h else [], rechazados=h.rechazados_turno if h else [],
            escalamientos=h.escalamientos_turno if h else [], informes=h.informes_turno if h else [],
            documentos=h.documentos_turno if h else [], herramientas=usadas or [],
            enmascarados=enmascarados or {}, modo=modo, modelo=modelo, latencia_ms=latencia,
            tokens=tokens, aviso=aviso)


__all__ = ["ErrorEntrada", "RespuestaChat", "ServicioChat"]
