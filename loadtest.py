import argparse, asyncio, time, httpx

PROMPTS = [
    "Explain what a Docker container is in two sentences.",
    "Write a haiku about servers.",
    "What is the capital of France?",
    "Summarize how HTTP works in three sentences.",
    "List three benefits of monitoring a service.",
]

async def one(client, i, url):
    start = time.perf_counter()
    try:
        r = await client.post(f"{url}/generate",
            json={"prompt": PROMPTS[i % len(PROMPTS)], "stream": False}, timeout=120)
        r.raise_for_status(); d = r.json()
        return time.perf_counter()-start, d.get("tokens",0), None
    except Exception as e:
        return time.perf_counter()-start, 0, str(e)

async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=int, default=30)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--url", default="http://localhost:8000")
    a = ap.parse_args()
    sem = asyncio.Semaphore(a.concurrency); res = []
    async with httpx.AsyncClient() as c:
        async def w(i):
            async with sem: res.append(await one(c, i, a.url))
        t0 = time.perf_counter()
        await asyncio.gather(*(w(i) for i in range(a.requests)))
        total = time.perf_counter()-t0
    ok = [r for r in res if r[2] is None]; err = [r for r in res if r[2]]
    lat = sorted(r[0] for r in ok); toks = sum(r[1] for r in ok)
    pct = lambda p: lat[min(len(lat)-1, int(len(lat)*p))] if lat else 0
    print(f"\n{'='*44}")
    print(f"requests={a.requests} concurrency={a.concurrency}")
    print(f"ok={len(ok)} errors={len(err)} wall={total:.1f}s")
    print(f"throughput={len(ok)/total:.1f} req/s  tokens={toks}")
    if lat: print(f"latency p50={pct(0.5):.2f}s p95={pct(0.95):.2f}s max={lat[-1]:.2f}s")
    if err: print(f"first error: {err[0][2]}")
    print(f"{'='*44}\n")

if __name__ == "__main__":
    asyncio.run(main())
