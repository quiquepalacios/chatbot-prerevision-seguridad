"""
llm_router.py — Un solo cliente para cualquier API de LLM.

La configuración no menciona marcas: siempre son las mismas variables.

    LLM_BASE_URL   endpoint compatible (o nativo) que quieras usar
    LLM_API_KEY    clave de ese endpoint
    LLM_MODEL      nombre del modelo tal como lo espera ese endpoint

Y opcionalmente:

    LLM_FAMILY          auto | openai | anthropic | gemini   (protocolo, no marca)
    LLM_REASONING       auto | off | low | medium | high
    LLM_REASONING_STYLE auto | none | effort | effort_obj | thinking | budget | enable_thinking
    LLM_TOOLS_MODE      auto | tools | anthropic | gemini | prompt | ninguno
    LLM_TIMEOUT         segundos
    LLM_2_BASE_URL / LLM_2_API_KEY / LLM_2_MODEL    (respaldo 2; hasta LLM_9_*)
    LLM_FALLBACKS       lista compacta: "url|clave|modelo; url|clave|modelo"

Los valores sí pueden ser concretos (un endpoint, una clave, un nombre de modelo);
lo que no cambia son los nombres de las variables.

Qué resuelve
------------
1. Cadena de respaldo: si el endpoint principal falla (401, 403, 404, 429,
   timeout, sin red, modelo inexistente), pasa al siguiente.
2. Cuatro modos de function calling, elegidos solos:
      tools        (formato OpenAI)
      anthropic    (input_schema)
      gemini       (functionDeclarations)
      prompt       (herramientas descritas en el prompt + JSON estricto)
   Si el endpoint rechaza `tools`, se reintenta igual en modo `prompt`.
3. Razonamiento traducido al estilo que soporte el modelo, con el razonamiento
   devuelto aparte (`RespuestaLLM.razonamiento`).
4. Salida estructurada (JSON) con reparación.

Uso
---
    from llm_router import crear_router
    r = crear_router()
    print(r.chat([{"role": "user", "content": "hola"}]).texto)

    resp = r.chat(mensajes, tools=[{"name": "consultar_factura",
                                    "description": "...",
                                    "parameters": {...}}])
    resp.tool_calls     # [{"id": ..., "name": ..., "arguments": {...}}]
    resp.razonamiento   # traza, separada del texto

Interfaces compatibles: `get_langchain_model()` (BaseChatModel) y `get_client()`
(`.chat.completions.create`, estilo SDK de OpenAI).

CLI
---
    python llm_router.py --list              # configuración activa
    python llm_router.py --probe             # prueba de vida de cada endpoint
    python llm_router.py --demo "hola"       # una conversación
    python llm_router.py --demo "hola" --tools
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence

__all__ = [
    "Endpoint",
    "RespuestaLLM",
    "Router",
    "SinProveedores",
    "crear_router",
    "get_client",
    "get_langchain_model",
    "listar_endpoints",
]

MODOS_HERRAMIENTAS = ("auto", "tools", "anthropic", "gemini", "prompt", "ninguno")
NIVELES_RAZONAMIENTO = ("auto", "off", "low", "medium", "high")
ESTILOS_RAZONAMIENTO = (
    "auto", "none", "effort", "effort_obj", "thinking", "budget", "enable_thinking", "modelo",
)
PRESUPUESTO = {"low": 512, "medium": 2048, "high": 8192}
FAMILIAS = ("auto", "openai", "anthropic", "gemini")


class SinProveedores(RuntimeError):
    """No hay ningún endpoint configurado o todos fallaron."""

    def __init__(self, mensaje: str, intentos: Sequence[str] = ()) -> None:
        super().__init__(mensaje)
        self.intentos = list(intentos)


@dataclass
class Endpoint:
    """Un destino concreto: a dónde, con qué clave y qué modelo."""

    base_url: str
    api_key: str = ""
    modelo: str = ""
    familia: str = "auto"
    nombre: str = "principal"

    def resumen(self) -> str:
        clave = "sin clave" if not self.api_key else f"clave {self.api_key[:4]}…"
        return f"{self.nombre}: {self.modelo or '(sin modelo)'} @ {self.base_url} [{clave}]"


@dataclass
class RespuestaLLM:
    """Respuesta normalizada, igual venga de donde venga."""

    texto: str = ""
    razonamiento: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    datos: Any = None
    endpoint: str = ""
    modelo: str = ""
    modo: str = ""
    familia: str = ""
    uso: dict[str, int] = field(default_factory=dict)
    latencia: float = 0.0
    crudo: Any = None

    @property
    def ok(self) -> bool:
        return bool(self.texto or self.tool_calls or self.datos)


# ===========================================================================
# Lectura de configuración
# ===========================================================================
def _env(nombre: str, defecto: str = "") -> str:
    valor = os.getenv(nombre)
    return valor.strip() if valor and valor.strip() else defecto


def _limpiar_clave(valor: str | None) -> str:
    """Descarta claves de plantilla que el alumno deja sin rellenar."""
    if not valor:
        return ""
    v = valor.strip().strip('"').strip("'")
    minus = v.lower()
    for marca in ("tu_api_key", "aqui", "xxxx", "your_api", "cambia_esta", "sk-xxx", "tu-key"):
        if marca in minus:
            return ""
    return v


def _es_local(url: str) -> bool:
    u = url.lower()
    return any(x in u for x in ("127.0.0.1", "localhost", "0.0.0.0", "::1"))


def _leer_endpoints() -> list[Endpoint]:
    """Principal + respaldos, sin nombrar ninguna marca."""
    lista: list[Endpoint] = []

    principal = Endpoint(
        base_url=_env("LLM_BASE_URL").rstrip("/"),
        api_key=_limpiar_clave(_env("LLM_API_KEY")),
        modelo=_env("LLM_MODEL"),
        familia=_env("LLM_FAMILY", "auto").lower(),
        nombre="principal",
    )
    if principal.base_url:
        lista.append(principal)

    for i in range(2, 10):
        base = _env(f"LLM_{i}_BASE_URL").rstrip("/")
        if not base:
            continue
        lista.append(Endpoint(
            base_url=base,
            api_key=_limpiar_clave(_env(f"LLM_{i}_API_KEY")),
            modelo=_env(f"LLM_{i}_MODEL"),
            familia=_env(f"LLM_{i}_FAMILY", "auto").lower(),
            nombre=f"respaldo {i - 1}",
        ))

    for j, trozo in enumerate(_env("LLM_FALLBACKS").split(";"), 1):
        partes = [p.strip() for p in trozo.split("|")]
        if not partes or not partes[0]:
            continue
        lista.append(Endpoint(
            base_url=partes[0].rstrip("/"),
            api_key=_limpiar_clave(partes[1]) if len(partes) > 1 else "",
            modelo=partes[2] if len(partes) > 2 else "",
            familia=(partes[3].lower() if len(partes) > 3 else "auto"),
            nombre=f"respaldo {j}",
        ))

    # Un endpoint remoto sin clave no sirve; uno local sí (no pide clave).
    return [e for e in lista if e.api_key or _es_local(e.base_url)]


def _familia_de(endpoint: Endpoint) -> str:
    """Protocolo del endpoint. `auto` lo deduce de la URL, sin tabla de marcas."""
    pedida = (endpoint.familia or "auto").lower()
    if pedida in ("openai", "anthropic", "gemini"):
        return pedida
    url = endpoint.base_url.lower()
    if "anthropic" in url:
        return "anthropic"
    if "generativelanguage" in url or "generatecontent" in url:
        return "gemini"
    return "openai"  # /chat/completions: el más extendido


def _estilo_razonamiento(endpoint: Endpoint, familia: str) -> str:
    """Qué parámetro activa el razonamiento en este modelo.

    Se puede fijar con LLM_REASONING_STYLE; si no, se deduce del nombre del
    modelo (que es un valor, no una marca) y, en último caso, de la familia.
    """
    pedido = _env("LLM_REASONING_STYLE", "auto").lower()
    if pedido in ESTILOS_RAZONAMIENTO and pedido != "auto":
        return pedido
    modelo = (endpoint.modelo or "").lower()
    if familia == "anthropic":
        return "thinking"
    if familia == "gemini":
        return "budget"
    if "reasoner" in modelo or "-r1" in modelo:
        return "modelo"
    if re.match(r"^(o\d|gpt-5)", modelo):
        return "effort"
    if "qwen3" in modelo or "thinking" in modelo:
        return "enable_thinking"
    if "grok" in modelo:
        return "effort"
    return "none"


# ===========================================================================
# Utilidades internas
# ===========================================================================
def _http_post(url: str, payload: dict, headers: dict, timeout: float) -> tuple[int, dict | str]:
    cuerpo = json.dumps(payload).encode("utf-8")
    try:
        import httpx  # type: ignore

        with httpx.Client(timeout=timeout) as cliente:
            r = cliente.post(url, content=cuerpo, headers=headers)
            texto = r.text
            try:
                return r.status_code, json.loads(texto) if texto else {}
            except json.JSONDecodeError:
                return r.status_code, texto
    except ImportError:
        pass
    except Exception as exc:  # red, DNS, TLS
        return 0, f"{type(exc).__name__}: {exc}"

    import urllib.error
    import urllib.request

    peticion = urllib.request.Request(url, data=cuerpo, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            texto = respuesta.read().decode("utf-8", "replace")
            try:
                return respuesta.status, json.loads(texto)
            except json.JSONDecodeError:
                return respuesta.status, texto
    except urllib.error.HTTPError as exc:
        texto = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(texto)
        except json.JSONDecodeError:
            return exc.code, texto
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}"


_ERRORES_CAPACIDAD = (
    "tools", "tool_choice", "function", "functions", "response_format",
    "json_schema", "reasoning_effort", "thinking", "unsupported", "not supported",
    "invalid_request", "unknown parameter", "extra fields", "unrecognized",
)
_ERRORES_CLAVE = ("invalid api key", "unauthorized", "authentication", "api key", "401", "403")
_ERRORES_MODELO = ("model_not_found", "does not exist", "not found", "unknown model", "404")


def _clasificar_error(status: int, cuerpo: Any) -> str:
    texto = cuerpo[:600].lower() if isinstance(cuerpo, str) else json.dumps(cuerpo)[:600].lower()
    if status in (401, 403) or any(m in texto for m in _ERRORES_CLAVE):
        return "clave"
    if status == 404 or any(m in texto for m in _ERRORES_MODELO):
        return "modelo"
    if status == 429 or "quota" in texto or "rate limit" in texto:
        return "cuota"
    if status == 0:
        return "red"
    if status in (400, 422) and any(m in texto for m in _ERRORES_CAPACIDAD):
        return "capacidad"
    if status >= 500:
        return "red"
    return "otro"


def _json_extraer(texto: str) -> Any:
    if not texto:
        return None
    limpio = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto.strip(), flags=re.S)
    try:
        return json.loads(limpio)
    except json.JSONDecodeError:
        pass
    for abre, cierra in (("{", "}"), ("[", "]")):
        inicio = limpio.find(abre)
        if inicio < 0:
            continue
        nivel = 0
        for i in range(inicio, len(limpio)):
            if limpio[i] == abre:
                nivel += 1
            elif limpio[i] == cierra:
                nivel -= 1
                if nivel == 0:
                    fragmento = limpio[inicio : i + 1]
                    try:
                        return json.loads(fragmento)
                    except json.JSONDecodeError:
                        try:
                            return json.loads(fragmento.replace("'", '"'))
                        except json.JSONDecodeError:
                            break
    return None


def _mensajes_a_dicts(mensajes: Any) -> list[dict[str, Any]]:
    """Normaliza mensajes conservando lo que necesita el bucle de herramientas"""
    salida: list[dict[str, Any]] = []
    for m in mensajes:
        if isinstance(m, dict):
            limpio: dict[str, Any] = {"role": m.get("role", "user"),
                                      "content": m.get("content") or ""}
            # assistant con tool_calls y el mensaje `tool` que le responde
            if m.get("tool_calls"):
                limpio["tool_calls"] = m["tool_calls"]
            if m.get("tool_call_id"):
                limpio["tool_call_id"] = m["tool_call_id"]
            if m.get("name"):
                limpio["name"] = m["name"]
            salida.append(limpio)
            continue
        if isinstance(m, (tuple, list)) and len(m) == 2:
            rol, contenido = m
            salida.append({"role": str(rol), "content": str(contenido)})
            continue
        rol = getattr(m, "type", None) or getattr(m, "role", "user")
        mapa = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}
        salida.append({"role": mapa.get(str(rol), str(rol)), "content": getattr(m, "content", str(m))})
    return salida


def _normalizar_tools(tools: Sequence[dict] | None) -> list[dict]:
    if not tools:
        return []
    salida = []
    for t in tools:
        if "function" in t:
            fn = t["function"]
            salida.append({
                "name": fn.get("name"),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        else:
            salida.append({
                "name": t.get("name"),
                "description": t.get("description", ""),
                "parameters": t.get("parameters") or {"type": "object", "properties": {}},
            })
    return salida


def _describir_herramientas(tools: Sequence[dict]) -> str:
    lineas = [
        "Tienes estas herramientas. Para usarlas responde SOLO con un JSON:",
        '{"tool": "<nombre>", "arguments": {<argumentos>}}',
        "Si no necesitas ninguna, responde en texto normal.",
        "",
    ]
    for t in tools:
        lineas.append(f"- {t.get('name')}: {t.get('description', '')}")
        params = t.get("parameters") or {}
        if params.get("properties"):
            props = ", ".join(
                f"{k} ({v.get('type', 'any')})" for k, v in params["properties"].items()
            )
            lineas.append(f"  argumentos: {props}")
    return "\n".join(lineas)


def _tool_desde_texto(texto: str) -> dict | None:
    """Modo prompt: '{"tool": ..., "arguments": {...}}' -> tool_call."""
    datos = _json_extraer(texto)
    if not isinstance(datos, dict):
        return None
    nombre = datos.get("tool") or datos.get("name") or datos.get("function")
    if isinstance(nombre, dict):
        argumentos = nombre.get("arguments", {})
        nombre = nombre.get("name")
    else:
        argumentos = datos.get("arguments") or datos.get("parameters") or datos.get("args") or {}
    if not isinstance(nombre, str) or not nombre:
        return None
    if isinstance(argumentos, str):
        argumentos = _json_extraer(argumentos) or {}
    if not isinstance(argumentos, dict):
        argumentos = {}
    return {"id": f"prompt_{uuid.uuid4().hex[:8]}", "name": nombre, "arguments": argumentos}


def _gemini_esquema(esquema: dict) -> dict:
    if not isinstance(esquema, dict):
        return esquema
    permitido = {"type", "format", "description", "nullable", "enum", "items",
                 "properties", "required", "minimum", "maximum", "minItems", "maxItems"}
    salida = {k: v for k, v in esquema.items() if k in permitido}
    if "properties" in salida:
        salida["properties"] = {k: _gemini_esquema(v) for k, v in salida["properties"].items()}
    if "items" in salida:
        salida["items"] = _gemini_esquema(salida["items"])
    if isinstance(salida.get("type"), str):
        salida["type"] = salida["type"].upper()
    return salida


# ===========================================================================
# Router
# ===========================================================================
class Router:
    """Cliente único con cadena de respaldo y traducción de formatos."""

    def __init__(
        self,
        endpoints: Sequence[Endpoint] | None = None,
        modelo: str | None = None,
        razonamiento: str | None = None,
        modo_herramientas: str | None = None,
        timeout: float | None = None,
        verbose: bool | None = None,
        max_intentos: int = 4,
    ) -> None:
        self.endpoints = list(endpoints) if endpoints else _leer_endpoints()
        if modelo and self.endpoints:
            self.endpoints[0].modelo = modelo
        self.razonamiento = (razonamiento or _env("LLM_REASONING", "auto")).lower()
        self.modo_herramientas = (
            modo_herramientas or _env("LLM_TOOLS_MODE", "auto")
        ).lower()
        self.timeout = float(timeout or _env("LLM_TIMEOUT", "60"))
        self.verbose = bool(verbose if verbose is not None else _env("LLM_DEBUG") == "1")
        self.max_intentos = max(1, int(max_intentos))
        self.historial: list[dict[str, Any]] = []
        self._capacidad: dict[str, str] = {}

    def _log(self, mensaje: str) -> None:
        if self.verbose:
            print(f"[llm_router] {mensaje}")

    def candidatos(self) -> list[tuple[Endpoint, str]]:
        """Endpoints utilizables con su familia ya resuelta."""
        salida = []
        for endpoint in self.endpoints[: self.max_intentos]:
            if not endpoint.modelo:
                self._log(f"{endpoint.nombre}: sin modelo, se omite (define LLM_MODEL)")
                continue
            salida.append((endpoint, _familia_de(endpoint)))
        return salida

    # --- API principal -----------------------------------------------------
    def chat(
        self,
        mensajes: Any,
        *,
        tools: Sequence[dict] | None = None,
        json_schema: dict | None = None,
        temperature: float = 0.4,
        max_tokens: int = 600,
        razonamiento: str | None = None,
        sistema: str | None = None,
    ) -> RespuestaLLM:
        lista = _mensajes_a_dicts(mensajes)
        if sistema and not any(m["role"] == "system" for m in lista):
            lista.insert(0, {"role": "system", "content": sistema})
        tools_norm = _normalizar_tools(tools)
        nivel = (razonamiento or self.razonamiento).lower()

        candidatos = self.candidatos()
        if not candidatos:
            raise SinProveedores(
                "No hay ningún endpoint configurado. Define LLM_BASE_URL, "
                "LLM_API_KEY y LLM_MODEL en el .env (y LLM_2_*, LLM_3_*… como respaldo)."
            )

        intentos: list[str] = []
        for endpoint, familia in candidatos:
            modos = self._modos_para(endpoint, familia, tools_norm, json_schema)
            for modo in modos:
                inicio = time.perf_counter()
                try:
                    resp = self._llamar(endpoint, familia, lista, tools_norm, json_schema,
                                        temperature, max_tokens, nivel, modo)
                except _ErrorProveedor as exc:
                    intentos.append(f"{endpoint.nombre}/{modo}: {exc.categoria} ({exc})")
                    self._log(f"fallo {endpoint.nombre} modo {modo}: {exc.categoria}")
                    if exc.categoria == "capacidad" and modo != "prompt":
                        self._capacidad[endpoint.nombre] = "prompt"
                        continue
                    break
                except Exception as exc:  # noqa: BLE001
                    intentos.append(f"{endpoint.nombre}/{modo}: {type(exc).__name__} ({exc})")
                    break
                resp.latencia = time.perf_counter() - inicio
                self._log(f"ok {endpoint.nombre} ({resp.modelo}) modo {modo} en {resp.latencia:.2f}s")
                self.historial.append({
                    "endpoint": endpoint.nombre, "modelo": resp.modelo, "modo": modo,
                    "ms": int(resp.latencia * 1000), "ok": True,
                })
                return resp

        self.historial.append({"endpoint": "-", "ok": False, "intentos": intentos})
        raise SinProveedores(
            "Ningún endpoint respondió. Revisa la clave, la red o la cuota.\n  - "
            + "\n  - ".join(intentos or ["sin candidatos"]),
            intentos,
        )

    def stream(self, mensajes: Any, **kw: Any) -> Iterator[str]:
        texto = self.chat(mensajes, **kw).texto
        for i in range(0, len(texto), 24):
            yield texto[i : i + 24]

    def disponible(self) -> tuple[bool, str]:
        try:
            r = self.chat([{"role": "user", "content": "ping"}], max_tokens=8, temperature=0)
            return True, f"{r.modelo} en {r.latencia:.2f}s"
        except SinProveedores as exc:
            return False, str(exc).splitlines()[0]
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}: {exc}"

    # --- elección de modo --------------------------------------------------
    def _modos_para(
        self,
        endpoint: Endpoint,
        familia: str,
        tools: list[dict],
        json_schema: dict | None,
    ) -> list[str]:
        if self.modo_herramientas == "ninguno" or (not tools and not json_schema):
            return ["tools" if self.modo_herramientas == "tools" else "prompt"]
        fijado = self._capacidad.get(endpoint.nombre) or self.modo_herramientas
        if fijado != "auto":
            return [fijado] if fijado == "prompt" else [fijado, "prompt"]
        preferido = {"anthropic": "anthropic", "gemini": "gemini"}.get(familia, "tools")
        return [preferido, "prompt"] if preferido != "prompt" else ["prompt"]

    # --- llamada por familia ----------------------------------------------
    def _llamar(self, endpoint, familia, mensajes, tools, json_schema,
                temperature, max_tokens, nivel, modo) -> RespuestaLLM:
        if familia == "anthropic":
            return self._anthropic(endpoint, mensajes, tools, json_schema,
                                   temperature, max_tokens, nivel, modo)
        if familia == "gemini":
            return self._gemini(endpoint, mensajes, tools, json_schema,
                                temperature, max_tokens, nivel, modo)
        return self._openai(endpoint, mensajes, tools, json_schema,
                           temperature, max_tokens, nivel, modo)

    # --- protocolo compatible /chat/completions ---------------------------
    def _openai(self, endpoint, mensajes, tools, json_schema,
                temperature, max_tokens, nivel, modo) -> RespuestaLLM:
        modelo = endpoint.modelo
        url = f"{endpoint.base_url}/chat/completions"
        cuerpo: dict[str, Any] = {
            "model": modelo,
            "messages": list(mensajes),
            "temperature": temperature,
        }
        usa_completion_tokens = bool(re.match(r"^(o\d|gpt-5)", modelo or ""))
        cuerpo["max_completion_tokens" if usa_completion_tokens else "max_tokens"] = max_tokens

        if modo == "tools" and tools:
            cuerpo["tools"] = [
                {"type": "function", "function": {
                    "name": t["name"], "description": t["description"],
                    "parameters": t["parameters"]}}
                for t in tools
            ]
            cuerpo["tool_choice"] = "auto"
        elif modo == "prompt" and tools:
            cuerpo["messages"] = self._con_catalogo(mensajes, tools)

        if json_schema:
            if modo == "prompt":
                cuerpo["messages"] = self._con_esquema(cuerpo["messages"], json_schema)
            else:
                cuerpo["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "respuesta", "schema": json_schema, "strict": False},
                }

        estilo = _estilo_razonamiento(endpoint, "openai")
        if nivel != "off":
            presupuesto = PRESUPUESTO.get(nivel, 2048)
            efectivo = "medium" if nivel == "auto" else nivel
            if estilo == "enable_thinking":
                cuerpo["extra_body"] = {"enable_thinking": True, "thinking_budget": presupuesto}
            elif estilo == "effort" and usa_completion_tokens:
                cuerpo["reasoning_effort"] = efectivo
            elif estilo == "effort_obj":
                cuerpo["reasoning"] = {"effort": efectivo}

        # Un endpoint local no lleva clave: no se envía la cabecera vacía.
        cabeceras = {"Content-Type": "application/json"}
        if endpoint.api_key:
            cabeceras["Authorization"] = f"Bearer {endpoint.api_key}"
        status, datos = _http_post(url, cuerpo, cabeceras, self.timeout)
        if status != 200:
            raise _ErrorProveedor(status, datos)

        eleccion = (datos.get("choices") or [{}])[0]
        mensaje = eleccion.get("message") or {}
        texto = mensaje.get("content") or ""
        razonamiento = (
            mensaje.get("reasoning_content")
            or mensaje.get("reasoning")
            or (datos.get("reasoning") if isinstance(datos.get("reasoning"), str) else "")
            or ""
        )
        llamadas = []
        for i, tc in enumerate(mensaje.get("tool_calls") or []):
            fn = tc.get("function") or {}
            llamadas.append({
                "id": tc.get("id") or f"call_{i}_{uuid.uuid4().hex[:6]}",
                "name": fn.get("name", ""),
                "arguments": _json_extraer(fn.get("arguments") or "{}") or {},
            })
        respuesta = RespuestaLLM(
            texto=texto, razonamiento=razonamiento, tool_calls=llamadas,
            endpoint=endpoint.nombre, modelo=datos.get("model") or modelo,
            modo=modo, familia="openai", uso=datos.get("usage") or {}, crudo=datos,
        )
        if json_schema:
            respuesta.datos = _json_extraer(texto)
            if respuesta.datos is None and modo != "prompt":
                raise _ErrorProveedor(400, "respuesta no es JSON válido (capacidad)")
        if modo == "prompt" and tools and not respuesta.tool_calls:
            llamada = _tool_desde_texto(texto)
            if llamada:
                respuesta.tool_calls = [llamada]
        return respuesta

    # --- protocolo nativo /v1/messages -----------------------------------
    def _anthropic(self, endpoint, mensajes, tools, json_schema,
                   temperature, max_tokens, nivel, modo) -> RespuestaLLM:
        sistema = "\n".join(m["content"] for m in mensajes if m["role"] == "system")
        charla = [m for m in mensajes if m["role"] != "system"]
        if modo == "prompt" and tools:
            sistema += "\n\n" + _describir_herramientas(tools)
        if json_schema:
            sistema += "\n\nResponde SOLO con JSON que cumpla este esquema:\n" + json.dumps(json_schema)

        cuerpo: dict[str, Any] = {
            "model": endpoint.modelo,
            "max_tokens": max_tokens,
            "messages": charla,
        }
        if sistema:
            cuerpo["system"] = sistema
        if modo == "anthropic" and tools:
            cuerpo["tools"] = [
                {"name": t["name"], "description": t["description"],
                 "input_schema": t["parameters"]} for t in tools
            ]
        estilo = _estilo_razonamiento(endpoint, "anthropic")
        if nivel != "off" and estilo == "thinking":
            presupuesto = PRESUPUESTO.get("medium" if nivel == "auto" else nivel, 2048)
            if max_tokens > presupuesto:
                cuerpo["thinking"] = {"type": "enabled", "budget_tokens": presupuesto}
            else:
                cuerpo["temperature"] = temperature
        else:
            cuerpo["temperature"] = temperature

        cabeceras_ant = {"Content-Type": "application/json",
                         "anthropic-version": "2023-06-01"}
        if endpoint.api_key:
            cabeceras_ant["x-api-key"] = endpoint.api_key
        status, datos = _http_post(
            f"{endpoint.base_url}/v1/messages", cuerpo, cabeceras_ant, self.timeout,
        )
        if status != 200:
            raise _ErrorProveedor(status, datos)

        textos, razonamientos, llamadas = [], [], []
        for bloque in datos.get("content") or []:
            tipo = bloque.get("type")
            if tipo == "text":
                textos.append(bloque.get("text", ""))
            elif tipo in ("thinking", "redacted_thinking"):
                razonamientos.append(bloque.get("thinking", ""))
            elif tipo == "tool_use":
                llamadas.append({
                    "id": bloque.get("id") or f"toolu_{uuid.uuid4().hex[:8]}",
                    "name": bloque.get("name", ""),
                    "arguments": bloque.get("input") or {},
                })
        texto = "".join(textos)
        respuesta = RespuestaLLM(
            texto=texto, razonamiento="\n".join(r for r in razonamientos if r),
            tool_calls=llamadas, endpoint=endpoint.nombre,
            modelo=datos.get("model") or endpoint.modelo, modo=modo, familia="anthropic",
            uso={k: v for k, v in (datos.get("usage") or {}).items() if isinstance(v, int)},
            crudo=datos,
        )
        if json_schema:
            respuesta.datos = _json_extraer(texto)
        if modo == "prompt" and tools and not respuesta.tool_calls:
            llamada = _tool_desde_texto(texto)
            if llamada:
                respuesta.tool_calls = [llamada]
        return respuesta

    # --- protocolo nativo :generateContent -------------------------------
    def _gemini(self, endpoint, mensajes, tools, json_schema,
                temperature, max_tokens, nivel, modo) -> RespuestaLLM:
        sistema = "\n".join(m["content"] for m in mensajes if m["role"] == "system")
        if modo == "prompt" and tools:
            sistema += "\n\n" + _describir_herramientas(tools)
        if json_schema:
            sistema += "\n\nResponde SOLO con JSON que cumpla este esquema:\n" + json.dumps(json_schema)

        contenidos = [
            {"role": "model" if m["role"] == "assistant" else "user",
             "parts": [{"text": m["content"]}]}
            for m in mensajes if m["role"] != "system"
        ]
        generacion: dict[str, Any] = {"temperature": temperature, "maxOutputTokens": max_tokens}
        if json_schema and modo != "prompt":
            generacion["responseMimeType"] = "application/json"
            generacion["responseSchema"] = _gemini_esquema(json_schema)
        estilo = _estilo_razonamiento(endpoint, "gemini")
        if estilo == "budget":
            if nivel == "off":
                generacion["thinkingConfig"] = {"thinkingBudget": 0}
            else:
                generacion["thinkingConfig"] = {
                    "thinkingBudget": PRESUPUESTO.get("medium" if nivel == "auto" else nivel, 2048)
                }

        cuerpo: dict[str, Any] = {"contents": contenidos, "generationConfig": generacion}
        if sistema:
            cuerpo["systemInstruction"] = {"parts": [{"text": sistema}]}
        if modo == "gemini" and tools:
            cuerpo["tools"] = [{"functionDeclarations": [
                {"name": t["name"], "description": t["description"],
                 "parameters": _gemini_esquema(t["parameters"])} for t in tools
            ]}]

        url = f"{endpoint.base_url}/models/{endpoint.modelo}:generateContent?key={endpoint.api_key}"
        status, datos = _http_post(url, cuerpo, {"Content-Type": "application/json"}, self.timeout)
        if status != 200:
            raise _ErrorProveedor(status, datos)

        candidato = (datos.get("candidates") or [{}])[0]
        partes = (candidato.get("content") or {}).get("parts") or []
        textos, razonamientos, llamadas = [], [], []
        for parte in partes:
            if "text" in parte and not parte.get("thought"):
                textos.append(parte["text"])
            elif parte.get("thought"):
                razonamientos.append(parte.get("text", ""))
            if "functionCall" in parte:
                fc = parte["functionCall"]
                llamadas.append({"id": f"gem_{uuid.uuid4().hex[:8]}",
                                 "name": fc.get("name", ""), "arguments": fc.get("args") or {}})
        texto = "".join(textos)
        uso = datos.get("usageMetadata") or {}
        respuesta = RespuestaLLM(
            texto=texto, razonamiento="\n".join(r for r in razonamientos if r),
            tool_calls=llamadas, endpoint=endpoint.nombre, modelo=endpoint.modelo,
            modo=modo, familia="gemini",
            uso={"prompt_tokens": uso.get("promptTokenCount", 0),
                 "completion_tokens": uso.get("candidatesTokenCount", 0)},
            crudo=datos,
        )
        if json_schema:
            respuesta.datos = _json_extraer(texto)
        return respuesta

    # --- ayudas de prompt --------------------------------------------------
    @staticmethod
    def _con_catalogo(mensajes: list[dict[str, str]], tools: list[dict]) -> list[dict[str, str]]:
        copia = [dict(m) for m in mensajes]
        catalogo = _describir_herramientas(tools)
        for m in copia:
            if m["role"] == "system":
                m["content"] = f"{m['content']}\n\n{catalogo}"
                return copia
        copia.insert(0, {"role": "system", "content": catalogo})
        return copia

    @staticmethod
    def _con_esquema(mensajes: list[dict[str, str]], esquema: dict) -> list[dict[str, str]]:
        copia = [dict(m) for m in mensajes]
        regla = ("Responde SOLO con un objeto JSON válido que cumpla este esquema:\n"
                 + json.dumps(esquema))
        for m in copia:
            if m["role"] == "system":
                m["content"] = f"{m['content']}\n\n{regla}"
                return copia
        copia.insert(0, {"role": "system", "content": regla})
        return copia


class _ErrorProveedor(RuntimeError):
    """Error interno con la categoría ya clasificada."""

    def __init__(self, status: int, cuerpo: Any) -> None:
        self.status = status
        self.categoria = _clasificar_error(status, cuerpo)
        detalle = cuerpo if isinstance(cuerpo, str) else json.dumps(cuerpo)[:300]
        super().__init__(f"{status}: {detalle}")


# ===========================================================================
# Interfaces compatibles
# ===========================================================================
def crear_router(**kw: Any) -> Router:
    return Router(**kw)


def get_client(**kw: Any) -> "RouterOpenAI":
    return RouterOpenAI(Router(**kw))


def get_langchain_model(**kw: Any):
    return RouterChatModel(router=Router(**kw))


class _Completions:
    def __init__(self, router: Router) -> None:
        self._router = router

    def create(self, **kw: Any):
        esquema = None
        formato = kw.get("response_format") or {}
        if isinstance(formato, dict) and formato.get("type") == "json_schema":
            esquema = (formato.get("json_schema") or {}).get("schema")
        resp = self._router.chat(
            kw.get("messages") or [],
            tools=kw.get("tools"),
            json_schema=esquema,
            temperature=float(kw.get("temperature", 0.4)),
            max_tokens=int(kw.get("max_tokens") or kw.get("max_completion_tokens") or 600),
        )
        from urllib.parse import urlparse

        host = ""
        for endpoint in self._router.endpoints:
            if endpoint.nombre == resp.endpoint:
                host = urlparse(endpoint.base_url).netloc or endpoint.base_url
                break
        return _completado_estilo_openai(resp, host)


class RouterOpenAI:
    """Emula la superficie del SDK (`client.chat.completions.create`)."""

    def __init__(self, router: Router) -> None:
        self.router = router
        self.chat = type("Chat", (), {"completions": _Completions(router)})()


def _completado_estilo_openai(resp: RespuestaLLM, host: str = ""):
    from types import SimpleNamespace as NS

    herramienta = [
        NS(id=tc["id"], type="function",
           function=NS(name=tc["name"], arguments=json.dumps(tc["arguments"])))
        for tc in resp.tool_calls
    ]
    mensaje = NS(content=resp.texto, role="assistant",
                 tool_calls=herramienta or None,
                 reasoning_content=resp.razonamiento)
    uso = NS(prompt_tokens=resp.uso.get("prompt_tokens", 0),
             completion_tokens=resp.uso.get("completion_tokens", 0),
             total_tokens=resp.uso.get("total_tokens", 0))
    return NS(choices=[NS(message=mensaje, finish_reason="stop", index=0)],
              model=resp.modelo, usage=uso,
              _endpoint=resp.endpoint, _host=host or resp.endpoint, _modo=resp.modo)


class RouterChatModel:
    """Drop-in de un modelo de chat de LangChain, con respaldo entre endpoints."""

    def __new__(cls, router: Router | None = None, **kw: Any):
        from langchain_core.language_models.chat_models import BaseChatModel
        from langchain_core.messages import AIMessage, AIMessageChunk
        from langchain_core.outputs import ChatGeneration, ChatResult

        class _Modelo(BaseChatModel):
            router: Any = None
            temperatura: float = 0.4
            max_tokens: int = 600

            model_config = {"arbitrary_types_allowed": True}

            @property
            def _llm_type(self) -> str:
                return "llm-router"

            @property
            def _identifying_params(self) -> dict[str, Any]:
                return {"router": "multi-endpoint"}

            @property
            def nombre_modelo(self) -> str:
                """Nombre del modelo del endpoint principal (para mostrarlo en clase)."""
                try:
                    candidatos = self.router.candidatos() if self.router else []
                except Exception:  # noqa: BLE001
                    candidatos = []
                if candidatos:
                    endpoint = candidatos[0][0]
                    return f"{endpoint.modelo} ({endpoint.nombre})" if len(candidatos) == 1 \
                        else f"{endpoint.modelo} + {len(candidatos) - 1} respaldo(s)"
                return "llm-router"

            def _generate(self, messages, stop=None, run_manager=None, **kwargs):
                r = self.router or Router(**kw)
                resp = r.chat(_mensajes_a_dicts(messages),
                              temperature=kwargs.get("temperature", self.temperatura),
                              max_tokens=kwargs.get("max_tokens", self.max_tokens))
                mensaje = AIMessage(
                    content=resp.texto,
                    additional_kwargs={"razonamiento": resp.razonamiento,
                                       "endpoint": resp.endpoint,
                                       "modelo": resp.modelo,
                                       "modo": resp.modo},
                    usage_metadata={"input_tokens": resp.uso.get("prompt_tokens", 0),
                                    "output_tokens": resp.uso.get("completion_tokens", 0),
                                    "total_tokens": resp.uso.get("total_tokens", 0)},
                )
                return ChatResult(generations=[ChatGeneration(message=mensaje)])

            def _stream(self, messages, stop=None, run_manager=None, **kwargs):
                from langchain_core.messages import AIMessageChunk
                from langchain_core.outputs import ChatGenerationChunk

                r = self.router or Router(**kw)
                for trozo in r.stream(_mensajes_a_dicts(messages),
                                      temperature=kwargs.get("temperature", self.temperatura),
                                      max_tokens=kwargs.get("max_tokens", self.max_tokens)):
                    yield ChatGenerationChunk(message=AIMessageChunk(content=trozo))

        return _Modelo(router=router or Router(**kw),
                       temperatura=kw.get("temperature", 0.4),
                       max_tokens=kw.get("max_tokens", 600))


# ===========================================================================
# Inspección (CLI)
# ===========================================================================
def listar_endpoints() -> list[dict[str, Any]]:
    filas = []
    for e in _leer_endpoints():
        familia = _familia_de(e)
        filas.append({
            "nombre": e.nombre,
            "base_url": e.base_url,
            "modelo": e.modelo or "(falta LLM_MODEL)",
            "familia": familia,
            "estilo_razonamiento": _estilo_razonamiento(e, familia),
            "clave": "configurada" if e.api_key else ("no hace falta (local)" if _es_local(e.base_url) else "falta"),
        })
    return filas


def _cli(argv: Sequence[str]) -> int:
    router = Router()
    if "--list" in argv or len(argv) == 0:
        filas = listar_endpoints()
        if not filas:
            print("Sin endpoints configurados.")
            print("Define en el .env:")
            print("  LLM_BASE_URL=https://…/v1")
            print("  LLM_API_KEY=…")
            print("  LLM_MODEL=…")
            print("Respaldos opcionales: LLM_2_BASE_URL / LLM_2_API_KEY / LLM_2_MODEL")
            return 1
        print(f"{'endpoint':12} {'modelo':26} {'protocolo':10} {'razonamiento':14} clave")
        print("-" * 86)
        for f in filas:
            print(f"{f['nombre']:12} {f['modelo'][:26]:26} {f['familia']:10} "
                  f"{f['estilo_razonamiento']:14} {f['clave']}")
        print("\nCadena activa:", " -> ".join(f["nombre"] for f in filas))
        return 0

    if "--probe" in argv:
        if not router.candidatos():
            print("Sin endpoints utilizables (revisa LLM_BASE_URL / LLM_API_KEY / LLM_MODEL).")
            return 1
        fallos = 0
        for endpoint, _ in router.candidatos():
            unico = Router(endpoints=[endpoint])
            ok, detalle = unico.disponible()
            print(f"[{'OK ' if ok else 'FALLA'}] {endpoint.nombre:12} {endpoint.modelo:26} {detalle}")
            fallos += 0 if ok else 1
        return 0 if fallos == 0 else 1

    if "--demo" in argv:
        i = argv.index("--demo")
        texto = argv[i + 1] if len(argv) > i + 1 else "Hola"
        tools = None
        if "--tools" in argv:
            tools = [{
                "name": "consultar_factura",
                "description": "Devuelve el detalle de una factura del cliente.",
                "parameters": {"type": "object",
                               "properties": {"numero": {"type": "string"}},
                               "required": ["numero"]},
            }]
        resp = router.chat([{"role": "user", "content": texto}], tools=tools)
        print(f"endpoint={resp.endpoint} modelo={resp.modelo} modo={resp.modo} {resp.latencia:.2f}s")
        if resp.razonamiento:
            print(f"razonamiento: {resp.razonamiento[:400]}")
        print(f"texto: {resp.texto}")
        if resp.tool_calls:
            print("tool_calls:", json.dumps(resp.tool_calls, ensure_ascii=False))
        return 0

    print(__doc__)
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_cli(sys.argv[1:]))
