"""Cómo arma el router la llamada a OpenAI, sin red: se captura el cuerpo enviado."""
from __future__ import annotations

import pytest

from app.llm import llm_router
from app.llm.llm_router import Endpoint, Router

HERRAMIENTA = [{"name": "buscar_lineamiento", "description": "busca",
                "parameters": {"type": "object", "properties": {}}}]


@pytest.fixture()
def capturar(monkeypatch):
    enviados: list[dict] = []

    def falso(url, cuerpo, cabeceras, timeout):
        enviados.append({"url": url, "cuerpo": cuerpo})
        return 200, {"model": cuerpo["model"], "usage": {},
                     "choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(llm_router, "_http_post", falso)
    monkeypatch.delenv("LLM_REASONING_STYLE", raising=False)
    return enviados


def _llamar(modelo: str, razonamiento: str = "auto") -> dict:
    router = Router(endpoints=[Endpoint("https://api.openai.com/v1", "sk-prueba", modelo)],
                    razonamiento=razonamiento, modo_herramientas="tools")
    router.chat([{"role": "user", "content": "hola"}], tools=HERRAMIENTA,
                temperature=0.2, max_tokens=16000)


@pytest.mark.parametrize("modelo", ["gpt-6.2-sol", "gpt-6.2-astra", "gpt-5.4-mini", "o3"])
def test_modelos_de_razonamiento(capturar, modelo):
    _llamar(modelo)
    cuerpo = capturar[-1]["cuerpo"]
    assert capturar[-1]["url"].endswith("/chat/completions")
    assert cuerpo["max_completion_tokens"] == 16000 and "max_tokens" not in cuerpo
    assert "temperature" not in cuerpo                 # solo aceptan la de por defecto
    assert cuerpo["reasoning_effort"] == "medium"      # LLM_REASONING=auto
    assert cuerpo["tools"][0]["function"]["name"] == "buscar_lineamiento"


@pytest.mark.parametrize("nivel,esperado", [("low", "low"), ("high", "high")])
def test_nivel_de_razonamiento_configurable(capturar, nivel, esperado):
    _llamar("gpt-6.2-sol", nivel)
    assert capturar[-1]["cuerpo"]["reasoning_effort"] == esperado


def test_razonamiento_apagado_no_envia_none(capturar):
    """gpt-6.2-sol rechaza reasoning_effort="none": con off no se envía el parámetro."""
    _llamar("gpt-6.2-sol", "off")
    assert "reasoning_effort" not in capturar[-1]["cuerpo"]


@pytest.mark.parametrize("modelo", ["gpt-4.1-mini", "deepseek-chat", "llama3"])
def test_modelos_clasicos_mantienen_temperature_y_max_tokens(capturar, modelo):
    _llamar(modelo)
    cuerpo = capturar[-1]["cuerpo"]
    assert cuerpo["temperature"] == 0.2 and cuerpo["max_tokens"] == 16000
    assert "max_completion_tokens" not in cuerpo and "reasoning_effort" not in cuerpo
