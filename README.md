# LLM Inference Service

A self-hosted LLM inference service with production-grade observability. An open-weight model is served behind a FastAPI gateway that adds bounded-concurrency request handling, token streaming, health probes, and Prometheus metrics — with Grafana dashboards for latency, throughput, and queue pressure. The whole stack runs with a single `docker-compose up`.

This is the layer that turns a model into a *service*: not "can the model answer," but "does it stay fast and observable under real, concurrent load."

## Architecture

```
            +--------------+     +--------------+     +------------+
 client --> |   gateway    | --> |    ollama    | --> |   model    |
            |  (FastAPI)   |     | (inference)  |     +------------+
            +------+-------+     +--------------+
                   | /metrics
                   v
            +--------------+     +--------------+
            |  prometheus  | --> |   grafana    |
            +--------------+     +--------------+
```

- **gateway** — FastAPI. OpenAI-style `/generate` (streaming and non-streaming), a bounded concurrency semaphore so the backend queues instead of overloading, `/health` (liveness) and `/ready` (backend reachability) probes, and a `/metrics` endpoint.
- **ollama** — serves an open-weight model (default `llama3.2:1b`).
- **prometheus** — scrapes gateway metrics every 5s.
- **grafana** — pre-provisioned dashboard, no manual setup.

## What it measures

- **Request latency** (p50 / p95 / p99)
- **Time to first token** — what decides whether streaming feels live
- **Tokens per second** — real throughput per request
- **In-flight requests & queue depth** — how deep the queue gets under load
- **Request rate** and **total tokens generated**

## Quick start

Requires Docker and Docker Compose. (Ollama can run on the host or as a container.)

```bash
# Start the stack
docker-compose up -d

# Pull a model (if using host Ollama)
ollama pull llama3.2:1b

# Send a request
curl -s localhost:8000/generate -H "Content-Type: application/json" \
  -d '{"prompt":"Explain what a reverse proxy is in two sentences.","stream":false}'
```

Open:
- Gateway API docs — http://localhost:8000/docs
- Prometheus — http://localhost:9090
- Grafana — http://localhost:3000 (dashboard: *LLM Inference Service*)

## Generate load and watch it move

```bash
python3 loadtest.py --requests 40 --concurrency 8
```

As concurrency exceeds `MAX_CONCURRENCY`, queue depth rises while in-flight stays capped — graceful backpressure instead of overload. Measured locally: 30 concurrent requests, 0 errors, p50 latency ~2.3s on a 1B model (CPU).

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MODEL` | `llama3.2:1b` | Model tag to serve |
| `MAX_CONCURRENCY` | `4` | Max simultaneous inferences; the rest queue |
| `REQUEST_TIMEOUT` | `120` | Per-request timeout (seconds) |

## Design notes

- **Bounded concurrency over unbounded.** Past a certain point, accepting every request degrades all of them. A semaphore caps in-flight work and queues the rest, keeping latency predictable under burst.
- **Liveness vs readiness are separate.** `/health` says the gateway is up; `/ready` checks the model backend. They fail independently — what an orchestrator needs to route traffic correctly.
- **Metrics at the gateway.** Measuring at the gateway captures what the client actually experiences, including queue time.

## Tech stack

Python · FastAPI · Ollama · Docker Compose · Prometheus · Grafana

## License

MIT
