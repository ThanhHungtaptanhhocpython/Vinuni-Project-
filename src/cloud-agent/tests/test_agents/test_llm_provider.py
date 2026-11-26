"""Unit tests cho lựa chọn provider OpenAI / OpenRouter / Gemini (không gọi network)."""

import pytest
from config import Settings
from services.llm import get_llm


def test_openai_provider_defaults():
    settings = Settings(
        llm_provider="openai",
        openai_api_key="sk-test",
        model_name="gpt-4o",
        model_fallback="gpt-4o-mini",
    )
    llm = get_llm(settings=settings)
    assert llm.model_name == "gpt-4o"
    # Không ép base_url OpenRouter
    assert "openrouter" not in (str(llm.openai_api_base) if llm.openai_api_base else "")


def test_openrouter_prefixes_short_model_names():
    settings = Settings(
        llm_provider="openrouter",
        openrouter_api_key="sk-or-test",
        model_name="gpt-4o",
        model_fallback="gpt-4o-mini",
        openrouter_app_title="Test Agent",
        openrouter_http_referer="https://example.com",
    )
    assert settings.resolve_model() == "openai/gpt-4o"
    assert settings.resolve_model("gpt-4o-mini") == "openai/gpt-4o-mini"
    assert settings.resolve_model("anthropic/claude-3.5-sonnet") == "anthropic/claude-3.5-sonnet"

    llm = get_llm(settings=settings)
    assert llm.model_name == "openai/gpt-4o"
    assert "openrouter.ai" in str(llm.openai_api_base)


def test_openrouter_falls_back_to_openai_api_key_env():
    settings = Settings(
        llm_provider="openrouter",
        openai_api_key="sk-or-from-openai-env",
        openrouter_api_key="",
    )
    assert settings.llm_api_key() == "sk-or-from-openai-env"


def test_gemini_resolve_model_and_api_key():
    settings = Settings(
        llm_provider="gemini",
        gemini_api_key="AIza-test",
        model_name="gemini-2.0-flash",
        model_fallback="gemini-2.0-flash-lite",
    )
    assert settings.resolve_model() == "gemini-2.0-flash"
    assert settings.resolve_model("google/gemini-1.5-pro") == "gemini-1.5-pro"
    assert settings.llm_api_key() == "AIza-test"


def test_gemini_get_llm_requires_package_or_returns_model():
    settings = Settings(
        llm_provider="gemini",
        gemini_api_key="AIza-test",
        model_name="gemini-2.0-flash",
    )
    try:
        llm = get_llm(settings=settings)
    except ImportError:
        pytest.skip("langchain-google-genai not installed")
    model = getattr(llm, "model", None) or getattr(llm, "model_name", None)
    assert model is not None
    assert "gemini" in str(model).lower()
