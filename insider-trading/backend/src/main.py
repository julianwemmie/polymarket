import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from src.database import init_db
from src.routers import leaderboard, markets, wallets
from src.tasks.ingest import ingest_markets, progress


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Polymarket Insider Trading Detector", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(markets.router)
app.include_router(wallets.router)
app.include_router(leaderboard.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/ingest")
async def trigger_ingest(limit: int = Query(default=15)):
    """Kick off ingestion in the background."""
    if progress["running"]:
        return {"status": "already_running", "progress": progress}
    asyncio.create_task(ingest_markets(limit=limit))
    return {"status": "started", "limit": limit}


@app.get("/api/ingest/progress")
async def ingest_progress():
    """SSE endpoint — stream ingestion progress to the browser."""
    import json

    async def event_stream():
        while True:
            data = json.dumps({
                "running": progress["running"],
                "current": progress["current"],
                "total": progress["total"],
                "current_market": progress["current_market"],
                "done_count": len(progress["markets_done"]),
                "error": progress["error"],
            })
            yield f"data: {data}\n\n"
            if not progress["running"] and progress["total"] > 0:
                yield f"data: {json.dumps({'done': True})}\n\n"
                break
            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
