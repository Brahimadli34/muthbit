"""عميل موحّد للنماذج اللغوية، بلا اعتماد على Django ولا على مكتبات المزوّدين.

نمطان فقط يغطيان أغلب المزوّدين:
- openai: أي واجهة متوافقة مع OpenAI (OpenAI، DeepSeek، Groq، Mistral، Gemini عبر رابطه المتوافق...)
- anthropic: واجهة Claude
"""
from dataclasses import dataclass

import requests

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


class LLMError(Exception):
    pass


@dataclass
class ProviderConfig:
    name: str
    api_style: str          # "openai" | "anthropic"
    model: str
    api_key: str
    base_url: str = ""
    timeout: int = 60


def _join(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def complete(cfg: ProviderConfig, system: str, user: str, max_tokens: int = 1500,
             temperature: float = 0.0) -> str:
    """يرسل رسالة واحدة ويرجع النص. يرفع LLMError عند أي فشل."""
    if not cfg.api_key:
        raise LLMError(f"لا يوجد مفتاح API للمزوّد {cfg.name}")
    try:
        if cfg.api_style == "anthropic":
            r = requests.post(
                _join(cfg.base_url, "v1/messages") if cfg.base_url else ANTHROPIC_URL,
                headers={"x-api-key": cfg.api_key, "anthropic-version": ANTHROPIC_VERSION,
                         "content-type": "application/json"},
                json={"model": cfg.model, "max_tokens": max_tokens, "temperature": temperature,
                      "system": system, "messages": [{"role": "user", "content": user}]},
                timeout=cfg.timeout,
            )
            r.raise_for_status()
            return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")

        if cfg.api_style == "openai":
            if not cfg.base_url:
                raise LLMError("نمط OpenAI يتطلب رابط الـ API")
            r = requests.post(
                _join(cfg.base_url, "chat/completions"),
                headers={"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"},
                json={"model": cfg.model, "max_tokens": max_tokens, "temperature": temperature,
                      "messages": [{"role": "system", "content": system},
                                   {"role": "user", "content": user}]},
                timeout=cfg.timeout,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"] or ""
    except requests.RequestException as exc:
        detail = ""
        if getattr(exc, "response", None) is not None:
            detail = f" ({exc.response.status_code}: {exc.response.text[:200]})"
        raise LLMError(f"فشل الاتصال بـ {cfg.name}{detail}") from exc
    except (KeyError, IndexError, ValueError) as exc:
        raise LLMError(f"استجابة غير متوقعة من {cfg.name}") from exc
    raise LLMError(f"نمط API غير معروف: {cfg.api_style}")
