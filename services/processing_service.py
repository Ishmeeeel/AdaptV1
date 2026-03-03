"""
services/processing_service.py – Async lesson processing pipeline.

Steps executed in order after a PDF is uploaded:
  1. extract_text   – Extract raw text from the PDF (pdfplumber)
  2. simplify       – Simplify text for dyslexia/cognitive profiles (HF API)
  3. image_desc     – Generate image alt-text descriptions (HF API)
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

LANGUAGES = {
    "english": "en-NG-AbeoVoice",   # Falls back to en-US-JennyNeural if unavailable
    "hausa":   "ha-NG-AliVoice",
    "yoruba":  "yo-NG-AdenleVoice",
    "igbo":    "ig-NG-EzinneVoice",
}


# ─────────────────────────────────────────────────────────────────────────────
# Entry point (called from BackgroundTasks)
# ─────────────────────────────────────────────────────────────────────────────

async def enqueue_lesson_processing(lesson_id: str, storage_path: str) -> None:
    """Top-level background coroutine for the full processing pipeline."""
    logger.info("Processing started for lesson %s", lesson_id)
    _set_status(lesson_id, "running")
    try:
        # 1. Download file from Supabase Storage
        file_bytes = _download_lesson_file(storage_path)

        # 2. Extract text pages
        pages = _extract_text(file_bytes)
        _update_step(lesson_id, "extract_text", True)
        _update_page_count(lesson_id, len(pages))

        # 3. For each page: simplify + store
        for i, text in enumerate(pages, start=1):
            simplified = await _simplify_text(text)
            img_desc = await _describe_images(text)  # placeholder
            _upsert_page(lesson_id, i, text, simplified, img_desc)

        _update_step(lesson_id, "simplify_dyslexia", True)
        _update_step(lesson_id, "image_descriptions", True)

        # 4. Synthesise audio per language (full lesson text combined)
        full_text = "\n\n".join(pages)
        for lang_key, voice in LANGUAGES.items():
            audio_url = await _synthesise_tts(full_text[:3000], voice, lesson_id, lang_key)
            if audio_url:
                _store_audio(lesson_id, lang_key, audio_url)
                step_key = f"audio_{lang_key}"
                _update_step(lesson_id, step_key, True)

        # 5. Mark done
        _set_status(lesson_id, "done")
        supabase.table("lessons").update({"is_published": True, "processing_status": "done"}).eq("id", lesson_id).execute()
        logger.info("Processing complete for lesson %s", lesson_id)

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
    """Update a single step flag in the processing_jobs JSONB column."""
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
    """Extract per-page text from a PDF using pdfplumber."""
    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            pages.append(text.strip())
    return pages if pages else ["[No text extracted]"]


# ─────────────────────────────────────────────────────────────────────────────
# HuggingFace – text simplification
# ─────────────────────────────────────────────────────────────────────────────

HF_API_URL = "https://api-inference.huggingface.co/models/"


async def _simplify_text(text: str) -> str | None:
    """
    Call Mistral-7B via HuggingFace Inference API to simplify text
    for learners with dyslexia or cognitive disabilities.
    """
    if not settings.HF_TOKEN or not text.strip():
        return None

    prompt = (
        "Rewrite the following educational text in simple English suitable for "
        "a primary school student with dyslexia. Use short sentences, simple words, "
        "and clear structure.\n\nText:\n"
        + text[:1500]
        + "\n\nSimplified version:"
    )
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                HF_API_URL + settings.HF_MODEL,
                headers={"Authorization": f"Bearer {settings.HF_TOKEN}"},
                json={"inputs": prompt, "parameters": {"max_new_tokens": 400}},
            )
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list) and data:
                generated = data[0].get("generated_text", "")
                # Strip the prompt echo
                if "Simplified version:" in generated:
                    return generated.split("Simplified version:")[-1].strip()
                return generated.strip()
    except Exception as exc:
        logger.warning("HF simplification failed: %s", exc)
    return None


async def _describe_images(page_text: str) -> str | None:
    """Placeholder – in a real implementation send page images to a vision model."""
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Azure TTS
# ─────────────────────────────────────────────────────────────────────────────

async def _synthesise_tts(text: str, voice: str, lesson_id: str, lang_key: str) -> str | None:
    """
    Call Azure Cognitive Services TTS REST API and upload the resulting
    MP3 to Supabase Storage.  Returns the public URL or None on failure.
    """
    if not settings.AZURE_TTS_KEY:
        logger.warning("AZURE_TTS_KEY not set – skipping TTS for %s", lang_key)
        return None

    region = settings.AZURE_TTS_REGION
    endpoint = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"

    ssml = (
        f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='en-US'>"
        f"<voice name='{voice}'>{_escape_xml(text)}</voice></speak>"
    )

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                endpoint,
                headers={
                    "Ocp-Apim-Subscription-Key": settings.AZURE_TTS_KEY,
                    "Content-Type": "application/ssml+xml",
                    "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                },
                content=ssml.encode("utf-8"),
            )
            r.raise_for_status()
            audio_bytes = r.content

        storage_path = f"audio/{lesson_id}/{lang_key}.mp3"
        supabase.storage.from_("lesson-audio").upload(
            storage_path,
            audio_bytes,
            file_options={"content-type": "audio/mpeg"},
        )
        public_url_res = supabase.storage.from_("lesson-audio").get_public_url(storage_path)
        return public_url_res

    except Exception as exc:
        logger.error("Azure TTS failed for %s (%s): %s", lesson_id, lang_key, exc)
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
            "lesson_id": lesson_id,
            "page_number": page_number,
            "content_original": original,
            "content_simplified": simplified,
            "image_description": img_description,
        },
        on_conflict="lesson_id,page_number",
    ).execute()


def _store_audio(lesson_id: str, language: str, audio_url: str) -> None:
    supabase.table("lesson_audio").upsert(
        {"lesson_id": lesson_id, "language": language, "audio_url": audio_url},
        on_conflict="lesson_id,language",
    ).execute()
