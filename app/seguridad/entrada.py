"""entrada.py — guardias que deciden ANTES de que el modelo vea el mensaje.

    mensaje
      ├─ 1. enmascarar   DNI, IP, tarjeta, número de cuenta, email, claves y tokens
      ├─ 2. bloquear     proyecto confidencial o "enfoque estratégico" -> no se llama
      │                  al modelo y el texto no se guarda (solo una marca)
      └─ 3. inyección    "ignora tus instrucciones", "aprueba sin revisar"... -> filtro

El enmascarado va primero para que ni el log ni la base de datos vean un dato
sensible, aunque el turno termine bloqueado. Las etiquetas que se registran son
genéricas ("dni", "ip"), nunca el valor.

Las palabras de la arquitectura no se tocan: "admin": true, "el gateway actúa
como proxy" o "en modo desarrollador se activa el debug" son parte del diseño
que hay que revisar, no un ataque. Por eso los patrones de inyección exigen un
imperativo dirigido al asistente.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app import config


# ---------------------------------------------------------------------------
# Texto
# ---------------------------------------------------------------------------
def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y sin signos, para comparar palabras clave."""
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", (texto or "").lower())
                         if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9 ]+", " ", sin_tildes).strip()


def _como_palabras(texto: str) -> str:
    return " " + " ".join(normalizar(texto).split()) + " "


# ---------------------------------------------------------------------------
# 1. Enmascarado de datos sensibles
# ---------------------------------------------------------------------------
def _luhn(digitos: str) -> bool:
    total, par = 0, False
    for d in reversed(digitos):
        n = int(d)
        if par:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
        par = not par
    return total % 10 == 0


RE_BLOQUE_CLAVE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                             re.DOTALL)
RE_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b")
RE_BEARER = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE)
RE_CLAVE_API = re.compile(
    r"\b(sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,})\b")
RE_URL_CON_CLAVE = re.compile(r"\b([a-z][a-z0-9+.-]*://[^\s:/@]+):([^\s@/]+)@", re.IGNORECASE)
RE_ASIGNACION = re.compile(
    r"\b(password|passwd|pwd|contrase(?:ñ|n)a|clave|secret|secreto|client_secret|api[_-]?key|"
    r"access[_-]?key|token|private[_-]?key)(\s*[:=]\s*)(\"[^\"]{3,}\"|'[^']{3,}'|[^\s,;)\]}]{3,})",
    re.IGNORECASE)
RE_PIN = re.compile(r"\b(cvv|cvc|pin|c[oó]digo de seguridad)\b(\D{0,12})(\d{3,6})\b", re.IGNORECASE)
RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
RE_IPV4 = re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)"
                     r"(?:/\d{1,2})?(?![\d.])")
RE_IPV6 = re.compile(r"(?<![\w:])(?:[0-9a-f]{0,4}:){2,7}[0-9a-f]{1,4}(?![\w:])", re.IGNORECASE)
# Al final: ni letra/dígito/guion ni ".5" o ",5" (un decimal); un punto final sí.
_FIN = r"(?![\w-]|[.,]\d)"
RE_CELULAR = re.compile(r"(?<![\w.-])(?:\+?51[ -]?)?9\d{2}[ -]?\d{3}[ -]?\d{3}" + _FIN)


def _parece_secreto(valor: str) -> bool:
    """Un valor entre comillas, o de 6+ caracteres con algún dígito o símbolo."""
    if valor[:1] in "\"'":
        return True
    return len(valor) >= 6 and bool(re.search(r"[\d\W_]", valor))
RE_NUMERO_LARGO = re.compile(r"(?<![\w.-])\d(?:[ -]?\d){9,22}" + _FIN)
RE_DNI = re.compile(r"(?<![\w.-])\d{8}" + _FIN)


def enmascarar(texto: str) -> tuple[str, list[str]]:
    """Reemplaza datos sensibles por una marca. Devuelve (texto, tipos encontrados).

    El orden importa: primero lo más específico (un JWT contiene dígitos; una
    tarjeta es un número largo; una IP no debe confundirse con un DNI).
    """
    tipos: list[str] = []

    def marca(tipo: str, etiqueta: str):
        def _sub(_m: re.Match[str]) -> str:
            tipos.append(tipo)
            return etiqueta
        return _sub

    texto = RE_BLOQUE_CLAVE.sub(marca("clave_privada", "[CLAVE PRIVADA REDACTADA]"), texto)
    texto = RE_JWT.sub(marca("token", "[TOKEN REDACTADO]"), texto)
    texto = RE_BEARER.sub(lambda m: (tipos.append("token") or f"{m.group(1)} [TOKEN REDACTADO]"), texto)
    texto = RE_CLAVE_API.sub(marca("clave", "[CLAVE REDACTADA]"), texto)
    texto = RE_URL_CON_CLAVE.sub(lambda m: (tipos.append("clave") or f"{m.group(1)}:[CLAVE REDACTADA]@"),
                                 texto)
    def _asignacion(m: re.Match[str]) -> str:
        # "Recuperar contraseña: OTP de 4 dígitos" describe el diseño, no es un secreto
        if not _parece_secreto(m.group(3)):
            return m.group(0)
        tipos.append("clave")
        return f"{m.group(1)}{m.group(2)}[CLAVE REDACTADA]"

    texto = RE_ASIGNACION.sub(_asignacion, texto)
    texto = RE_PIN.sub(lambda m: (tipos.append("clave") or f"{m.group(1)}{m.group(2)}[CLAVE REDACTADA]"),
                       texto)
    texto = RE_EMAIL.sub(marca("email", "[EMAIL REDACTADO]"), texto)

    def _ipv4(m: re.Match[str]) -> str:
        previo = texto_actual[max(0, m.start() - 12):m.start()].lower()
        if re.search(r"(versi[oó]n|version|\bv)\s*$", previo):      # "versión 2.1.0.3"
            return m.group(0)
        tipos.append("ip")
        return "[IP REDACTADA]"

    texto_actual = texto
    texto = RE_IPV4.sub(_ipv4, texto)

    def _ipv6(m: re.Match[str]) -> str:
        valor = m.group(0)
        # una hora "10:30:45" no es una IP: hace falta "::" o una letra hexadecimal
        if "::" not in valor and not re.search(r"[a-f]", valor, re.IGNORECASE):
            return valor
        tipos.append("ip")
        return "[IP REDACTADA]"

    texto = RE_IPV6.sub(_ipv6, texto)
    texto = RE_CELULAR.sub(marca("celular", "[CELULAR REDACTADO]"), texto)

    def _numero(m: re.Match[str]) -> str:
        digitos = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digitos) <= 19 and _luhn(digitos):
            tipos.append("tarjeta")
            return "[TARJETA REDACTADA]"
        tipos.append("numero_cuenta")
        return "[NÚMERO REDACTADO]"

    texto = RE_NUMERO_LARGO.sub(_numero, texto)
    texto = RE_DNI.sub(marca("dni", "[DNI REDACTADO]"), texto)
    return texto, tipos


# ---------------------------------------------------------------------------
# 2. Proyectos confidenciales y temas estratégicos
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Terminos:
    proyectos: tuple[tuple[str, tuple[str, ...]], ...]     # (nombre, alias)
    expresiones: tuple[str, ...]
    mensajes: dict[str, str] = field(default_factory=dict, hash=False, compare=False)


MENSAJES_POR_DEFECTO = {
    "proyecto": "Tu mensaje menciona un proyecto confidencial ({termino}). No puedo procesarlo: "
                "no se envió al modelo ni se guardó.",
    "expresion": "Tu mensaje toca un tema estratégico (\"{termino}\"). No se procesa con esta "
                 "herramienta: no se envió al modelo ni se guardó.",
}


@lru_cache(maxsize=4)
def cargar_terminos(ruta: Path | None = None) -> Terminos:
    ruta = Path(ruta or config.RUTA_TERMINOS)
    datos = json.loads(ruta.read_text("utf-8")) if ruta.exists() else {}
    proyectos = tuple((p["nombre"], tuple(p.get("alias", [])))
                      for p in datos.get("proyectos_confidenciales", []))
    expresiones = tuple(e["expresion"] for e in datos.get("expresiones_bloqueadas", []))
    return Terminos(proyectos, expresiones, {**MENSAJES_POR_DEFECTO, **datos.get("mensajes", {})})


def detectar_bloqueo(texto: str, terminos: Terminos | None = None) -> tuple[str, str] | None:
    """('proyecto' | 'expresion', término) si el texto no puede procesarse."""
    terminos = terminos or cargar_terminos()
    palabras = _como_palabras(texto)
    for nombre, alias in terminos.proyectos:
        for termino in (nombre, *alias):
            if _como_palabras(termino) in palabras:
                return "proyecto", nombre
    for expresion in terminos.expresiones:
        if _como_palabras(expresion) in palabras:
            return "expresion", expresion
    return None


# ---------------------------------------------------------------------------
# 3. Inyección de instrucciones
# ---------------------------------------------------------------------------
PATRONES_INYECCION = [
    r"\b(ignora|olvida|omite|desobedece|salta(te)?)\b.{0,30}\b(instrucciones|reglas|indicaciones|restricciones|politicas|prompt)\b",
    r"\b(ignore|forget|disregard)\b.{0,30}\b(instructions|rules|prompt|guidelines)\b",
    r"\b(muestra|revela|ensena|dime|repite|imprime)(me)?\b.{0,20}\b(tu|el|las|tus)\b.{0,10}\b(system prompt|prompt|instrucciones)\b",
    r"\b(reveal|show|print)\b.{0,20}\b(system prompt|your instructions)\b",
    r"\b(actua|comportate|finge|haz de cuenta)\b.{0,15}\b(como si fueras|como si no|que eres)\b",
    r"\b(ahora eres|a partir de ahora eres|desde ahora eres|you are now)\b",
    r"\b(activa|entra en|cambia a|pasa a)\b.{0,10}\bmodo\b.{0,5}\b(desarrollador|developer|dios|god|admin|sin restricciones|dan)\b",
    r"\bjailbreak\b",
    r"\b(prompt|instrucciones) del sistema\b",
    # dirigidas al revisor: que apruebe o que no reporte
    r"\b(aprueba|aprobar|marca como aprobad[oa]|da por aprobad[oa])\b.{0,40}\b(sin revisar|sin observaciones|automaticamente|directamente)\b",
    r"\b(no reportes|no menciones|omite|oculta)\b.{0,30}\b(hallazgos|vulnerabilidades|observaciones|riesgos)\b",
    r"\b(responde|di|contesta)\b.{0,20}\bque (todo esta bien|no hay hallazgos|esta aprobado|es seguro)\b",
]
_INYECCION = [re.compile(p) for p in PATRONES_INYECCION]


def detectar_inyeccion(texto: str) -> str | None:
    """Devuelve el patrón que coincidió, o None."""
    normalizado = " ".join(normalizar(texto).split())
    for patron in _INYECCION:
        if patron.search(normalizado):
            return patron.pattern
    return None


RESPUESTA_INYECCION = (
    "Detecté en el mensaje instrucciones dirigidas al asistente (por ejemplo, que ignore sus "
    "reglas o que apruebe sin revisar). No las sigo y no envié el mensaje al modelo. Si es parte "
    "de un documento de diseño, quita ese párrafo y vuelve a enviar la descripción técnica."
)


# ---------------------------------------------------------------------------
# Decisión
# ---------------------------------------------------------------------------
@dataclass
class DecisionEntrada:
    permitido: bool
    texto: str                         # ya enmascarado: lo único que sigue en el flujo
    enmascarados: list[str] = field(default_factory=list)
    motivo: str = ""                   # proyecto_confidencial | tema_estrategico | inyeccion
    detalle: str = ""                  # término o patrón (va al log, no al usuario)
    respuesta: str = ""                # texto fijo para el usuario si no se permite
    guardar_como: str = ""             # lo que se guarda en la DB en lugar del texto

    @property
    def resumen_enmascarado(self) -> dict[str, int]:
        conteo: dict[str, int] = {}
        for tipo in self.enmascarados:
            conteo[tipo] = conteo.get(tipo, 0) + 1
        return conteo


def revisar_entrada(texto: str, terminos: Terminos | None = None) -> DecisionEntrada:
    """Enmascara, bloquea temas no permitidos y detecta inyección, en ese orden."""
    terminos = terminos or cargar_terminos()
    limpio, tipos = enmascarar(texto)
    if bloqueo := detectar_bloqueo(limpio, terminos):
        tipo, termino = bloqueo
        motivo = "proyecto_confidencial" if tipo == "proyecto" else "tema_estrategico"
        return DecisionEntrada(
            False, limpio, tipos, motivo, termino,
            respuesta=terminos.mensajes[tipo].format(termino=termino),
            guardar_como=f"[mensaje no guardado: {motivo.replace('_', ' ')}]")
    if patron := detectar_inyeccion(limpio):
        return DecisionEntrada(False, limpio, tipos, "inyeccion", patron,
                               respuesta=RESPUESTA_INYECCION,
                               guardar_como="[mensaje no guardado: posible inyección de instrucciones]")
    return DecisionEntrada(True, limpio, tipos, guardar_como=limpio)


__all__ = ["DecisionEntrada", "Terminos", "cargar_terminos", "detectar_bloqueo",
           "detectar_inyeccion", "enmascarar", "normalizar", "revisar_entrada"]
