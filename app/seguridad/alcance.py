"""alcance.py — pedidos que el bot no atiende, con respuesta fija y sin modelo.

Lo que el bot NO hace (diapositiva 7):
- aprobar o rechazar pases a producción  -> eso lo decide Arquitectura de Seguridad
- revisar código o hacer pentest         -> herramientas SAST/DAST y el equipo de pentest
- operaciones de cliente (saldo, tarjeta) -> canales de atención del banco

Se responde sin llamar al modelo por dos razones: la respuesta es siempre la
misma (sirve para la demo y para las pruebas) y un modelo, ante "apruébame el
pase", podría terminar diciendo algo parecido a "aprobado".

Ojo con los falsos positivos: "¿cómo pido la aprobación de Seguridad?" es una
pregunta del proceso (la contesta la FAQ) y "la API genera estados de cuenta"
describe un diseño. Solo se reconocen pedidos dirigidos al bot.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.seguridad.entrada import normalizar


@dataclass(frozen=True)
class FueraDeAlcance:
    categoria: str          # aprobacion | codigo | cliente
    respuesta: str


PATRONES: dict[str, list[str]] = {
    "aprobacion": [
        r"\bapruebame\b", r"\bapruebalo\b", r"\bapruebala\b",
        r"\b(aprueba|apruebas|aprobarias|puedes aprobar|podrias aprobar)\b.{0,25}\b(pase|diseno|arquitectura|solucion|despliegue|release|salida)\b",
        r"\b(dame|danos|me das|nos das|me puedes dar)\b.{0,15}\b(visto bueno|ok|pase|aprobacion|luz verde|conformidad)\b",
        r"\b(puedo|podemos|ya puedo|ya podemos)\b.{0,10}\b(pasar|salir|desplegar|subir)\b.{0,6}\ba produccion\b",
        r"\b(esta|estamos|quedo|queda)\b.{0,10}\b(aprobad[oa]|listo para produccion|listos para produccion)\b\s*$",
        r"\b(autoriza|autorizame|firma|firmame)\b.{0,15}\b(el pase|la salida|el despliegue)\b",
        r"\b(approve|sign off)\b.{0,20}\b(release|deployment|design|go live|production)\b",
    ],
    "codigo": [
        r"\b(revisa|revisame|analiza|audita|escanea)\b.{0,15}\b(este|mi|el|nuestro|el siguiente)\b.{0,10}\b(codigo|repositorio|repo|script|pull request|pr)\b",
        r"\b(code review|revision de codigo)\b.{0,20}\b(de|para|a)\b",
        r"\b(haz|hazme|haznos|ejecuta|corre|lanza)\b.{0,10}\b(un |el )?(pentest|pentesting|escaneo|test de penetracion|nmap|sqlmap|ataque)\b",
        r"\b(encuentra|busca)\b.{0,10}\b(bugs|vulnerabilidades)\b.{0,10}\b(en este|en mi) codigo\b",
    ],
    "cliente": [
        r"\b(cual es|ver|consultar|consulta|revisar|dime)\b.{0,10}\bmi saldo\b",
        r"\bmi (tarjeta|cuenta de ahorros|cuenta corriente|prestamo|credito)\b.{0,30}\b(bloquead[oa]|saldo|movimientos|pago|cuota|clave)\b",
        r"\b(quiero|necesito|deseo)\b.{0,10}\b(transferir|abrir una cuenta|un prestamo|una tarjeta|pagar mi)\b",
        r"\b(bloquea|desbloquea|activa)\b.{0,10}\bmi tarjeta\b",
    ],
}

RESPUESTAS = {
    "aprobacion": (
        "No puedo aprobar ni rechazar un pase a producción: esa decisión es del área de "
        "Arquitectura de Seguridad (lineamientos LSA-2026). Lo que sí hago es una pre-revisión: "
        "si me describes la arquitectura (componentes, endpoints, autenticación, datos que "
        "maneja e integraciones), te indico los hallazgos con su fuente y qué puntos tienes que "
        "llevar a Seguridad para su revisión obligatoria."
    ),
    "codigo": (
        "No reviso código ni ejecuto pruebas de penetración: para eso están las herramientas de "
        "análisis de código (SAST/DAST) del pipeline y el equipo de pentest del banco. Yo reviso "
        "el diseño: cuéntame cómo está armada la solución (componentes, flujos, autenticación, "
        "datos e integraciones) y te digo qué lineamientos aplican."
    ),
    "cliente": (
        "Soy un asistente interno para revisar arquitecturas de software; no tengo acceso a "
        "cuentas, saldos ni tarjetas de clientes. Para eso usa la banca por internet, la app "
        "del banco o los canales de atención al cliente."
    ),
}

_COMPILADOS = {cat: [re.compile(p) for p in ps] for cat, ps in PATRONES.items()}


def detectar_fuera_de_alcance(texto: str) -> FueraDeAlcance | None:
    """Solo mensajes cortos: un documento de diseño largo que dice "dame el ok"
    al final se revisa igual (el modelo no aprueba, y la salida se valida)."""
    normalizado = " ".join(normalizar(texto).split())
    if len(normalizado.split()) > 80:
        return None
    for categoria, patrones in _COMPILADOS.items():
        if any(p.search(normalizado) for p in patrones):
            return FueraDeAlcance(categoria, RESPUESTAS[categoria])
    if parece_codigo(texto):
        return FueraDeAlcance("codigo", RESPUESTAS["codigo"])
    return None


RE_LINEA_CODIGO = re.compile(
    r"^\s*(def |class |import |from \S+ import|function |public |private |const |let |var |"
    r"return\b|if\s*\(|for\s*\(|#include|@app\.|SELECT |INSERT |UPDATE |\}|\{$|.*;\s*$)")


def parece_codigo(texto: str) -> bool:
    """Un bloque donde la mayoría de las líneas son código, no una descripción."""
    lineas = [l for l in texto.splitlines() if l.strip()]
    if len(lineas) < 8:
        return False
    codigo = sum(bool(RE_LINEA_CODIGO.match(l)) for l in lineas)
    return codigo / len(lineas) >= 0.5


__all__ = ["FueraDeAlcance", "RESPUESTAS", "detectar_fuera_de_alcance", "parece_codigo"]
