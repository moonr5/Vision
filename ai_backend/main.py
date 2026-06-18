# AI Backend — Fleet analysis, Gemini integration, and Telegram bot.
# Made by Monzer · github.com/moonr5/Vision
import os
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from analyzer import FleetAnalyzer
import db
import telegram_bot

load_dotenv()

_analyzer: Optional[FleetAnalyzer] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _analyzer

    # Database pool
    await db.init_pool()

    # Gemini AI analyzer
    api_key = os.getenv("GEMINI_API_KEY")
    if api_key:
        _analyzer = FleetAnalyzer(api_key)
        print("[AI] FleetAnalyzer ready (Gemini)")
    else:
        print("[AI] WARNING: GEMINI_API_KEY not set — /api/ai/analyze will return 503")

    # Telegram bot
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if tg_token:
        tg_chat_id = os.getenv("TELEGRAM_CHAT_ID")
        await telegram_bot.init_bot(tg_token, _analyzer, chat_id=tg_chat_id)
    else:
        print("[Telegram] TELEGRAM_BOT_TOKEN not set — bot disabled")

    yield

    await telegram_bot.shutdown_bot()
    await db.close_pool()


app = FastAPI(title="SGU Fleet AI Backend — Made by Monzer", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    question: str
    context: Optional[Dict[str, Any]] = None


class AnalyzeResponse(BaseModel):
    answer: str
    source: str = "gemini"


@app.get("/health")
def health():
    return {
        "status": "ok",
        "gemini_configured": _analyzer is not None,
        "telegram_active": telegram_bot.get_bot() is not None,
        "db_connected": db._pool is not None,
    }


@app.post("/api/ai/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest):
    if _analyzer is None:
        raise HTTPException(status_code=503, detail="GEMINI_API_KEY not configured")
    try:
        answer = _analyzer.analyze(req.question, req.context)
        return AnalyzeResponse(answer=answer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
