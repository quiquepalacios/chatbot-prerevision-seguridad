"""Cómo arma el router la llamada a OpenAI, sin red: se captura el cuerpo enviado."""
from __future__ import annotations

import json

import pytest

from app.llm import llm_router
from app.llm.llm_router import Endpoint, Router

HERRAMIENTA = [{"name": "buscar_lineamiento", "description": "busca",
                "parameters": {"type": "object", "properties": {}}}]
OPENAI = "https://api.openai.com/v1"

RESPUESTA_RESPONSES = {
    "model": "gpt-6.2-sol",
    "usage": {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
    "output": [
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "pienso"}]},
        {"type": "message", "content": [{"type": "output_text", "text": "Busco primero."}]},
        {"type": "function_call", "call_id": "call_1", "name": "buscar_lineamiento",
         "arguments": "{\"consulta\": \"BOLA\"}"},
    ],
}


@pytest.fixture()
def capturar(monkeypatch):
    enviados: list[dict] = []

    def falso(url, cuerpo, cabeceras, timeout):
        enviados.append({"url": url, "cuerpo": cuerpo})
        if url.endswith("/responses"):
            return 200, RESPUESTA_RESPONSES
        return 200, {"model": cuerpo["model"], "usage": {},
                     "choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(llm_router, "_http_post", falso)
    monkeypatch.delenv("LLM_REASONING_STYLE", raising=False)
    monkeypatch.delenv("LLM_OPENAI_API", raising=False)
    return enviados


def _llamar(modelo: str, razonamiento: str = "auto", base: str = OPENAI, mensajes=None):
    router = Router(endpoints=[Endpoint(base, "sk-prueba", modelo)],
                    razonamiento=razonamiento, modo_herramientas="tools")
    return router.chat(mensajes or [{"role": "user", "content": "hola"}], tools=HERRAMIENTA,
                       temperature=0.2, max_tokens=16000)


# --- /chat/completions (forzado con LLM_OPENAI_API=chat) ----------------------
@pytest.mark.parametrize("modelo", ["gpt-6.2-sol", "gpt-6.2-astra", "gpt-5.4-mini", "o3"])
def test_modelos_de_razonamiento_en_chat(capturar, monkeypatch, modelo):
    monkeypatch.setenv("LLM_OPENAI_API", "chat")
    _llamar(modelo)
    cuerpo = capturar[-1]["cuerpo"]
    assert capturar[-1]["url"].endswith("/chat/completions")
    assert cuerpo["max_completion_tokens"] == 16000 and "max_tokens" not in cuerpo
    assert "temperature" not in cuerpo                 # solo aceptan la de por defecto
    assert cuerpo["reasoning_effort"] == "medium"      # LLM_REASONING=auto
    assert cuerpo["tools"][0]["function"]["name"] == "buscar_lineamiento"


@pytest.mark.parametrize("nivel,esperado", [("low", "low"), ("high", "high")])
def test_nivel_de_razonamiento_configurable(capturar, monkeypatch, nivel, esperado):
    monkeypatch.setenv("LLM_OPENAI_API", "chat")
    _llamar("gpt-6.2-sol", nivel)
    assert capturar[-1]["cuerpo"]["reasoning_effort"] == esperado


def test_razonamiento_apagado_no_envia_none(capturar, monkeypatch):
    """gpt-6.2-sol rechaza reasoning_effort="none": con off no se envía el parámetro."""
    monkeypatch.setenv("LLM_OPENAI_API", "chat")
    _llamar("gpt-6.2-sol", "off")
    assert "reasoning_effort" not in capturar[-1]["cuerpo"]


@pytest.mark.parametrize("modelo", ["gpt-4.1-mini", "deepseek-chat", "llama3"])
def test_modelos_clasicos_mantienen_temperature_y_max_tokens(capturar, modelo):
    _llamar(modelo)
    cuerpo = capturar[-1]["cuerpo"]
    assert capturar[-1]["url"].endswith("/chat/completions")
    assert cuerpo["temperature"] == 0.2 and cuerpo["max_tokens"] == 16000
    assert "max_completion_tokens" not in cuerpo and "reasoning_effort" not in cuerpo


# --- /v1/responses: OpenAI no acepta tools + razonamiento en chat --------------
def test_razonamiento_con_tools_en_openai_usa_responses(capturar):
    mensajes = [
        {"role": "system", "content": "Eres el revisor."},
        {"role": "user", "content": "API con JWT"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_0", "type": "function",
             "function": {"name": "buscar_lineamiento", "arguments": "{\"consulta\": \"JWT\"}"}}]},
        {"role": "tool", "tool_call_id": "call_0", "name": "buscar_lineamiento",
         "content": "{\"resultados\": []}"},
    ]
    resp = _llamar("gpt-6.2-sol", "high", mensajes=mensajes)
    envio = capturar[-1]
    cuerpo = envio["cuerpo"]
    assert envio["url"] == f"{OPENAI}/responses"
    assert cuerpo["instructions"] == "Eres el revisor."
    assert cuerpo["input"] == [
        {"role": "user", "content": "API con JWT"},
        {"type": "function_call", "call_id": "call_0", "name": "buscar_lineamiento",
         "arguments": "{\"consulta\": \"JWT\"}"},
        {"type": "function_call_output", "call_id": "call_0", "output": "{\"resultados\": []}"},
    ]
    assert cuerpo["tools"] == [{"type": "function", "name": "buscar_lineamiento",
                                "description": "busca", "parameters": HERRAMIENTA[0]["parameters"],
                                "strict": False}]
    assert cuerpo["reasoning"] == {"effort": "high"} and cuerpo["max_output_tokens"] == 16000
    assert cuerpo["store"] is False and "temperature" not in cuerpo

    assert resp.texto == "Busco primero." and resp.razonamiento == "pienso"
    assert resp.tool_calls == [{"id": "call_1", "name": "buscar_lineamiento",
                                "arguments": {"consulta": "BOLA"}}]
    assert resp.uso["prompt_tokens"] == 120 and resp.uso["completion_tokens"] == 30


def test_otro_proveedor_compatible_sigue_en_chat(capturar):
    _llamar("gpt-6.2-sol", base="http://localhost:11434/v1")
    assert capturar[-1]["url"].endswith("/chat/completions")


def test_si_chat_pide_responses_se_cambia_y_se_recuerda(monkeypatch):
    """Un proxy compatible que devuelve el error de OpenAI: se reintenta en /responses."""
    urls: list[str] = []
    error = {"error": {"message": "Function tools with reasoning_effort are not supported for "
                                  "gpt-6.2-sol in /v1/chat/completions. To use function tools, "
                                  "use /v1/responses or set reasoning_effort to 'none'."}}

    def falso(url, cuerpo, cabeceras, timeout):
        urls.append(url)
        return (200, RESPUESTA_RESPONSES) if url.endswith("/responses") else (400, error)

    monkeypatch.setattr(llm_router, "_http_post", falso)
    monkeypatch.delenv("LLM_OPENAI_API", raising=False)
    router = Router(endpoints=[Endpoint("https://proxy.local/v1", "k", "gpt-6.2-sol")],
                    modo_herramientas="tools")
    for _ in range(2):
        resp = router.chat([{"role": "user", "content": "hola"}], tools=HERRAMIENTA)
        assert resp.tool_calls[0]["name"] == "buscar_lineamiento"
    assert [u.rsplit("/", 1)[-1] for u in urls] == ["completions", "responses", "responses"]


def test_responses_sin_tools_no_se_fuerza(capturar):
    router = Router(endpoints=[Endpoint(OPENAI, "sk-prueba", "gpt-6.2-sol")])
    router.chat([{"role": "user", "content": "hola"}])
    assert capturar[-1]["url"].endswith("/chat/completions")
    assert json.dumps(capturar[-1]["cuerpo"]).count("tools") == 0
