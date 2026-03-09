"""
services/processing_service.py – Async lesson processing pipeline.

Steps executed in order after a PDF is uploaded:
  1. extract_text   – Extract raw text from the PDF (pdfplumber)
  2. simplify       – Simplify text for dyslexia/cognitive profiles (HF Mistral-7B)
  3. image_desc     – Generate image alt-text descriptions (placeholder)
  4. audio_*        – Synthesise TTS in all 4 languages (Azure Neural TTS)

Each step updates the processing_jobs.steps flags so the teacher can
poll `/teacher/processing/{lesson_id}` and see real-time progress.
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Any, Dict

import httpx
import pdfplumber

from config import settings
from database import supabase

logger = logging.getLogger(__name__)

# ── Azure TTS voices (Nigerian voices, fallback to standard if unavailable) ──
LANGUAGES = {
    "english": "en-NG-AbeoVoice",
    "hausa":   "ha-NG-AliVoice",
    "yoruba":  "yo-NG-AdenleVoice",
    "igbo":    "ig-NG-EzinneVoice",
}

LANGUAGE_FALLBACKS = {
    "english": "en-US-JennyNeural",
    "hausa":   "en-US-JennyNeural",   # No Hausa fallback — use English
    "yoruba":  "en-US-JennyNeural",
    "igbo":    "en-US-JennyNeural",
}

HF_API_URL = "https://api-inference.huggingface.co/models/"

# Max chars sent to TTS per language (Azure limit is ~8000 chars for REST API)
TTS_MAX_CHARS = 6000

# Max chars sent to Mistral for simplification per page
SIMPLIFY_MAX_CHARS = 1500

# How many times to retry HF if model is loading (cold start)
HF_MAX_RETRIES = 3
HF_RETRY_DELAY = 20  # seconds to wait between retries


# ─────────────────────────────────────────────────────────────────────────────
# Entry point (called from BackgroundTasks)
# ─────────────────────────────────────────────────────────────────────────────

async def enqueue_lesson_processing(lesson_id: str, storage_path: str) -> None:
    """Top-level background coroutine for the full processing pipeline."""
    logger.info("Processing started for lesson %s", lesson_id)
    _set_status(lesson_id, "running")

    try:
        # 1. Download file from Supabase Storage
        logger.info("Downloading lesson file: %s", storage_path)
        file_bytes = _download_lesson_file(storage_path)

        # 2. Extract text pages
        pages = _extract_text(file_bytes)
        logger.info("Extracted %d pages from lesson %s", len(pages), lesson_id)
        _update_step(lesson_id, "extract_text", True)
        _update_page_count(lesson_id, len(pages))

        # 3. For each page: simplify + store (run simplifications concurrently)
        logger.info("Simplifying %d pages via Mistral…", len(pages))
        simplified_pages = await asyncio.gather(
            *[_simplify_text(text) for text in pages],
            return_exceptions=True,
        )

        for i, (text, simplified) in enumerate(zip(pages, simplified_pages), start=1):
            # If simplification threw an exception, treat as None
            if isinstance(simplified, Exception):
                logger.warning("Simplification failed for page %d: %s", i, simplified)
                simplified = None
            img_desc = await _describe_images(text)
            _upsert_page(lesson_id, i, text, simplified, img_desc)
            logger.info("Page %d stored (simplified=%s)", i, "yes" if simplified else "no")

        _update_step(lesson_id, "simplify_dyslexia", True)
        _update_step(lesson_id, "image_descriptions", True)

        # 4. Synthesise audio per language
        # Use full lesson text (all pages joined) up to TTS_MAX_CHARS
        full_text = "\n\n".join(p for p in pages if p.strip())
        logger.info("Starting TTS synthesis for lesson %s (%d chars)", lesson_id, len(full_text))

        for lang_key, voice in LANGUAGES.items():
            logger.info("Synthesising %s audio…", lang_key)
            audio_url = await _synthesise_tts(
                full_text[:TTS_MAX_CHARS], voice, lesson_id, lang_key,
                fallback_voice=LANGUAGE_FALLBACKS[lang_key]
            )
            if audio_url:
                _store_audio(lesson_id, lang_key, audio_url)
                _update_step(lesson_id, f"audio_{lang_key}", True)
                logger.info("Audio stored for %s", lang_key)
            else:
                logger.warning("Audio skipped for %s", lang_key)

        # 5. Mark done
        _set_status(lesson_id, "done")
        supabase.table("lessons").update({
            "is_published": True,
            "processing_status": "done",
        }).eq("id", lesson_id).execute()
        logger.info("✅ Processing complete for lesson %s", lesson_id)

    except Exception as exc:
        logger.error("Processing failed for lesson %s: %s", lesson_id, exc, exc_info=True)
        _set_status(lesson_id, "failed", error=str(exc))
        supabase.table("lessons").update({"processing_status": "failed"}).eq("id", lesson_id).execute()


# ─────────────────────────────────────────────────────────────────────────────
# Step helpers
# ─────────────────────────────────────────────────────────────────────────────

def _set_status(lesson_id: str, status: str, error: str | None = None) -> None:
    updates: Dict[str, Any] = {"status": status}
    if error:
        updates["error_message"] = error[:500]
    supabase.table("processing_jobs").update(updates).eq("lesson_id", lesson_id).execute()


def _update_step(lesson_id: str, step: str, value: bool) -> None:
    res = (
        supabase.table("processing_jobs")
        .select("steps")
        .eq("lesson_id", lesson_id)
        .single()
        .execute()
    )
    steps = (res.data or {}).get("steps") or {}
    steps[step] = value
    supabase.table("processing_jobs").update({"steps": steps}).eq("lesson_id", lesson_id).execute()


def _update_page_count(lesson_id: str, count: int) -> None:
    supabase.table("lessons").update({"page_count": count}).eq("id", lesson_id).execute()


# ─────────────────────────────────────────────────────────────────────────────
# File extraction
# ─────────────────────────────────────────────────────────────────────────────

def _download_lesson_file(storage_path: str) -> bytes:
    res = supabase.storage.from_("lesson-files").download(storage_path)
    return res


def _extract_text(file_bytes: bytes) -> list[str]:
    """Extract per-page text from a PDF using pdfplumber. Max 30 pages."""
    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        total = len(pdf.pages)
        limit = min(total, 30)
        logger.info("PDF has %d pages — processing first %d", total, limit)
        for page in pdf.pages[:limit]:
            text = page.extract_text() or ""
            cleaned = text.strip()
            if cleaned:
                pages.append(cleaned)
    return pages if pages else ["[No text could be extracted from this document]"]

# ─────────────────────────────────────────────────────────────────────────────
# HuggingFace – text simplification (Mistral-7B)
# ─────────────────────────────────────────────────────────────────────────────

async def _simplify_text(text: str) -> str | None:
    """
    Call Groq API (Llama3-8B) to simplify lesson text.
    Fast, free, no cold starts — replaces HuggingFace Mistral.
    """
    if not settings.GROQ_API_KEY or not text.strip():
        logger.warning("Groq API key not set or empty text — skipping simplification")
        return None

    prompt = (
        "You are a helpful teacher. A student with learning difficulties "
        "needs to understand the lesson below. Write a SHORT, CLEAR SUMMARY using:\n"
        "- Very short sentences (maximum 10 words each)\n"
        "- Simple everyday words a 10-year-old would understand\n"
        "- Bullet points (•) for the most important facts\n"
        "- End with 'What this means:' explaining the main idea in 2 sentences\n\n"
        "IMPORTANT: Do NOT copy sentences from the original. "
        "Write everything in your own simple words.\n\n"
        "Lesson text:\n"
        + text[:SIMPLIFY_MAX_CHARS]
    )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.GROQ_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama3-8b-8192",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 500,
                    "temperature": 0.3,
                },
            )
            r.raise_for_status()
            result = r.json()["choices"][0]["message"]["content"].strip()
            if len(result) < 40:
                logger.warning("Simplified text too short (%d chars), discarding", len(result))
                return None
            logger.info("✅ Groq simplified text generated (%d chars)", len(result))
            return result
    except Exception as exc:
        logger.warning("Groq simplification failed: %s", exc)
        return None
    
    for attempt in range(1, HF_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                r = await client.post(
                    HF_API_URL + settings.HF_MODEL,
                    headers={"Authorization": f"Bearer {settings.HF_TOKEN}"},
                    json={
                        "inputs": prompt,
                        "parameters": {
                            "max_new_tokens": 500,
                            "temperature": 0.3,
                            "repetition_penalty": 1.3,
                            "do_sample": True,
                        },
                    },
                )

                # 503 = model still loading — wait and retry
                if r.status_code == 503:
                    wait = HF_RETRY_DELAY * attempt
                    logger.warning(
                        "HF model loading (attempt %d/%d) — waiting %ds…",
                        attempt, HF_MAX_RETRIES, wait,
                    )
                    await asyncio.sleep(wait)
                    continue

                r.raise_for_status()
                data = r.json()

                if isinstance(data, list) and data:
                    generated = data[0].get("generated_text", "")

                    # Extract only what comes after the prompt
                    if "[/INST]" in generated:
                        result = generated.split("[/INST]")[-1].strip()
                    elif "Simple summary:" in generated:
                        result = generated.split("Simple summary:")[-1].strip()
                    else:
                        result = generated.strip()

                    # Sanity check — reject if too short or basically empty
                    if len(result) < 40:
                        logger.warning("Simplified text too short (%d chars), discarding", len(result))
                        return None

                    logger.info("Simplified text generated (%d chars)", len(result))
                    return result

        except httpx.TimeoutException:
            logger.warning("HF timeout on attempt %d/%d", attempt, HF_MAX_RETRIES)
            if attempt < HF_MAX_RETRIES:
                await asyncio.sleep(HF_RETRY_DELAY)
        except Exception as exc:
            logger.warning("HF simplification failed (attempt %d): %s", attempt, exc)
            if attempt < HF_MAX_RETRIES:
                await asyncio.sleep(HF_RETRY_DELAY)

    logger.error("HF simplification failed after %d attempts", HF_MAX_RETRIES)
    return None


async def _describe_images(page_text: str) -> str | None:
    """Placeholder – vision model integration deferred to v2."""
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Azure TTS
# ─────────────────────────────────────────────────────────────────────────────

async def _synthesise_tts(
    text: str,
    voice: str,
    lesson_id: str,
    lang_key: str,
    fallback_voice: str | None = None,
) -> str | None:
    """
    Call Azure Cognitive Services TTS REST API and upload the resulting
    MP3 to Supabase Storage. Returns the public URL or None on failure.
    Automatically retries with fallback voice if the primary voice fails.
    """
    if not settings.AZURE_TTS_KEY:
        logger.warning("AZURE_TTS_KEY not set — skipping TTS for %s", lang_key)
        return None

    voices_to_try = [voice]
    if fallback_voice and fallback_voice != voice:
        voices_to_try.append(fallback_voice)

    region   = settings.AZURE_TTS_REGION
    endpoint = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"

    for v in voices_to_try:
        ssml = (
            f"<speak version='1.0' "
            f"xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='en-US'>"
            f"<voice name='{v}'>{_escape_xml(text)}</voice></speak>"
        )
        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                r = await client.post(
                    endpoint,
                    headers={
                        "Ocp-Apim-Subscription-Key": settings.AZURE_TTS_KEY,
                        "Content-Type": "application/ssml+xml",
                        "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                    },
                    content=ssml.encode("utf-8"),
                )

                if r.status_code == 400 and fallback_voice and v != fallback_voice:
                    logger.warning("Voice %s not available, retrying with fallback %s", v, fallback_voice)
                    continue

                r.raise_for_status()
                audio_bytes = r.content

            storage_path = f"audio/{lesson_id}/{lang_key}.mp3"

            # Remove old file if exists (re-upload scenario)
            try:
                supabase.storage.from_("lesson-audio").remove([storage_path])
            except Exception:
                pass

            supabase.storage.from_("lesson-audio").upload(
                storage_path,
                audio_bytes,
                file_options={"content-type": "audio/mpeg"},
            )
            public_url = supabase.storage.from_("lesson-audio").get_public_url(storage_path)
            logger.info("TTS audio uploaded for %s using voice %s", lang_key, v)
            return public_url

        except Exception as exc:
            logger.error("Azure TTS failed for %s (%s) with voice %s: %s", lesson_id, lang_key, v, exc)

    return None


def _escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


# ─────────────────────────────────────────────────────────────────────────────
# Storage helpers
# ─────────────────────────────────────────────────────────────────────────────

def _upsert_page(
    lesson_id: str,
    page_number: int,
    original: str,
    simplified: str | None,
    img_description: str | None,
) -> None:
    supabase.table("lesson_pages").upsert(
        {
            "lesson_id":         lesson_id,
            "page_number":       page_number,
            "content_original":  original,
            "content_simplified": simplified,
            "image_description": img_description,
        },
        on_conflict="lesson_id,page_number",
    ).execute()


def _store_audio(lesson_id: str, language: str, audio_url: str) -> None:
    supabase.table("lesson_audio").upsert(
        {
            "lesson_id": lesson_id,
            "language":  language,
            "audio_url": audio_url,
        },
        on_conflict="lesson_id,language",
    ).execute()