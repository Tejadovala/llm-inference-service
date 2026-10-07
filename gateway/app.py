import asyncio, os, time, json
from contextlib import asynccontextmanager
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
MODEL = os.getenv("MODEL", "llama3.2:1b")
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY", "4"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "120"))

REQUESTS = Counter("llm_requests_total", "Total inference requests", ["endpoint", "status"])
LATENCY = Histogram("llm_request_latency_seconds", "End-to-end latency", ["endpoint"],
                    buckets=(0.1,0.25,0.5,1,2,4,8,16,32,64,120))
TOKENS = Counter("llm_tokens_generated_total", "Total output tokens")
TOKENS_PER_SEC = Histogram("llm_tokens_per_second", "Output tokens/sec",
                           buckets=(1,5,10,20,40,80,160,320))
TTFT = Histogram("llm_time_to_first_token_seconds", "Time to first token",
                 buckets=(0.05,0.1,0.25,0.5,1,2,4,8))
QUEUE_DEPTH = Gauge("llm_queue_depth", "Requests waiting or running")
INFLIGHT = Gauge("llm_inflight_requests", "Requests being processed")

@asynccontextmanager
async def lifespan(app):
    global _sem, _client
    _sem = asyncio.Semaphore(MAX_CONCURRENCY)
    _client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
    yield
    await _client.aclose()

app = FastAPI(title="LLM Inference Gateway", version="1.0.0", lifespan=lifespan)

class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    stream: bool = False
    temperature: float = 0.7
    max_tokens: int | None = None

@app.get("/health")
async def health(): return {"status": "ok"}

@app.get("/ready")
async def ready():
    try:
        r = await _client.get(f"{OLLAMA_URL}/api/tags", timeout=5); r.raise_for_status()
        return {"status": "ready", "backend": "up"}
    except Exception as e:
        return JSONResponse(status_code=503, content={"status":"not_ready","error":str(e)})

@app.get("/metrics")
async def metrics():
    return StreamingResponse(iter([generate_latest()]), media_type=CONTENT_TYPE_LATEST)

async def _stream(payload):
    start = time.perf_counter(); first = None; n = 0
    async with _client.stream("POST", f"{OLLAMA_URL}/api/generate", json=payload) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.strip(): continue
            obj = json.loads(line); chunk = obj.get("response","")
            if chunk:
                if first is None:
                    first = time.perf_counter(); TTFT.observe(first-start)
                n += 1; yield chunk
            if obj.get("done"): break
    elapsed = time.perf_counter()-start; TOKENS.inc(n)
    if elapsed>0 and n>0: TOKENS_PER_SEC.observe(n/elapsed)

@app.post("/generate")
async def generate(req: GenerateRequest):
    ep = "/generate"; QUEUE_DEPTH.inc(); queued = True; start = time.perf_counter()
    payload = {"model": MODEL, "prompt": req.prompt, "stream": req.stream,
               "options": {"temperature": req.temperature}}
    if req.max_tokens: payload["options"]["num_predict"] = req.max_tokens
    try:
        async with _sem:
            QUEUE_DEPTH.dec(); queued = False; INFLIGHT.inc()
            try:
                if req.stream:
                    async def gen():
                        async for c in _stream(payload): yield c
                        LATENCY.labels(ep).observe(time.perf_counter()-start)
                        REQUESTS.labels(ep,"success").inc()
                    return StreamingResponse(gen(), media_type="text/plain")
                r = await _client.post(f"{OLLAMA_URL}/api/generate", json=payload)
                r.raise_for_status(); data = r.json(); out = data.get("response","")
                n = data.get("eval_count", len(out.split())); TOKENS.inc(n)
                elapsed = time.perf_counter()-start; LATENCY.labels(ep).observe(elapsed)
                if elapsed>0 and n>0: TOKENS_PER_SEC.observe(n/elapsed)
                REQUESTS.labels(ep,"success").inc()
                return {"response": out, "tokens": n, "latency_s": elapsed}
            finally: INFLIGHT.dec()
    except httpx.HTTPError as e:
        if queued: QUEUE_DEPTH.dec()
        REQUESTS.labels(ep,"error").inc()
        raise HTTPException(status_code=502, detail=f"backend error: {e}")
