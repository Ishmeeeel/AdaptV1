"""
services/translation_service.py – UI string translation via Mistral-7B.
Translations are cached in the `translations` table to avoid re-processing.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from config import settings
from database import supabase

logger = logging.getLogger(__name__)

HF_API_URL = "https://api-inference.huggingface.co/models/"

LANGUAGE_NAMES = {
    "hausa":  "Hausa",
    "yoruba": "Yoruba",
    "igbo":   "Igbo",
}


async def translate_text(text: str, language: str) -> str:
    """
    Translate a UI string into the target language.
    Returns original text if language is english or translation fails.
    Caches result in `translations` table to avoid duplicate API calls.
    """
    if not text.strip():
        return text

    # English — no translation needed
    if language == "english" or language not in LANGUAGE_NAMES:
        return text

    # Check cache first
    cached = _get_cached(text, language)
    if cached:
        logger.info("Translation cache hit: %s → %s", language, text[:40])
        return cached

    # Call Mistral via HuggingFace
    translated = await _call_mistral(text, language)
    if not translated:
        # Fall back to original English if translation fails
        return text

    # Save to cache
    _save_cache(text, language, translated)
    return translated


def _get_cached(text: str, language: str) -> Optional[str]:
    try:
        res = (
            supabase.table("translations")
            .select("translated_text")
            .eq("source_text", text)
            .eq("language", language)
            .execute()
        )
        if res.data:
            return res.data[0]["translated_text"]
    except Exception as exc:
        logger.warning("Translation cache read failed: %s", exc)
    return None


def _save_cache(text: str, language: str, translated: str) -> None:
    try:
        supabase.table("translations").upsert(
            {
                "source_text":      text,
                "language":         language,
                "translated_text":  translated,
            },
            on_conflict="source_text,language",
        ).execute()
    except Exception as exc:
        logger.warning("Translation cache write failed: %s", exc)


async def _call_mistral(text: str, language: str) -> Optional[str]:
    if not settings.HF_TOKEN:
        logger.warning("HF_TOKEN not set — skipping translation")
        return None

    lang_name = LANGUAGE_NAMES[language]

    prompt = (
        f"Translate the following English text into {lang_name}. "
        f"Return ONLY the translated text with no explanation, no quotes, and nothing else.\n\n"
        f"English: {text}\n\n"
        f"{lang_name}:"
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                HF_API_URL + settings.HF_MODEL,
                headers={"Authorization": f"Bearer {settings.HF_TOKEN}"},
                json={
                    "inputs": prompt,
                    "parameters": {
                        "max_new_tokens": 200,
                        "temperature":    0.3,
                        "return_full_text": False,
                    },
                },
            )
            r.raise_for_status()
            data = r.json()

            if isinstance(data, list) and data:
                result = data[0].get("generated_text", "").strip()
                # Clean up any prompt echo
                for marker in [f"{lang_name}:", "Translation:", "Translated:"]:
                    if marker in result:
                        result = result.split(marker)[-1].strip()
                if result:
                    return result

    except Exception as exc:
        logger.error("Mistral translation failed: %s", exc)

    return None