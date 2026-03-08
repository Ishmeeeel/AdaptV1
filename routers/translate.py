"""
routers/translate.py – Translation endpoint for UI strings.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from dependencies import get_current_user
from services.translation_service import translate_text

router = APIRouter()


class TranslateRequest(BaseModel):
    text: str
    language: str


class TranslateResponse(BaseModel):
    translated: str
    language: str


@router.post("", response_model=TranslateResponse)
async def translate(body: TranslateRequest, user_id: str = Depends(get_current_user)):
    result = await translate_text(body.text, body.language)
    return TranslateResponse(translated=result, language=body.language)