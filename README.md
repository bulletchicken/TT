# TED 🧸

TED is a real-time, voice-first AI teddy bear that talks back *just enough* — but the architecture is the real star: streaming audio I/O, model-driven tool calls, and a long-term memory system backed by Supabase + pgvector.

---

## What it does
- **Real-time voice chat**: mic audio streams in, TED streams **text + speech audio** back out (with live transcripts).
- **Tool framework**: tools are registered once; the model can call them during the session.
- **Long-term memory**: TED saves useful highlights and retrieves relevant context later using vector search.
- **Optional wake word**: hands-free start via Porcupine (“Hey Ted”).

---

## Repo anatomy 🧠 (on purpose)
The codebase is laid out like a brain—because the responsibilities map cleanly:

- `TED/brain/prefrontal_cortex/` → real-time session control (streaming orchestration + state)
- `TED/brain/hippocampus/` → memory formation + recall (yes, literally the memory folder)
- `TED/brain/handlers/` → event routing (WebSocket streams, tool-call plumbing)
- `TED/brain/tools/` → tool implementations (the actual “abilities”)
- `TED/ears/` → wake word + audio entrypoints
- `TED/utils/` → shared helpers (audio, WS utilities, misc glue)

---

## Real-time voice pipeline
- Mic audio is streamed into **OpenAI Realtime**
- The model streams back:
  - incremental **transcripts**
  - **speech audio** for low-latency playback

Core file: `tt/brain/prefrontal_cortex/openai_realtime.py`

---

## Tool framework (the important part)
TED’s tools are built like a mini “plugin system”:
- **Register once** → tools become discoverable by the model
- **Schema-driven** → tools expose names + argument shapes so calls are reliable
- **Streaming tool calls** → tool-call events come over the WS, get parsed, executed, and results are sent back into the same live session
- **Threaded execution** → tools run without blocking audio/transcript streaming

Core files:
- `tt/brain/handlers/tools_plug.py`
- `tt/brain/handlers/websocket_tool_calls.py`
- `tt/brain/tools/`

Typical tools:
- time / weather
- reminders
- memory recall / save
- any API / side-effect actions you plug in

---

## Memory system (Supabase + pgvector)
TED’s memory is a simple, practical pipeline:

1. **Summarize** the session (keep it compact + useful)
2. **Extract highlights** (facts, preferences, “don’t forget this” moments)
3. **Embed** those highlights
4. **Store** in Supabase (pgvector)
5. **Recall** later by vector search and inject the best matches as context

Core files:
- `tt/brain/hippocampus/memorize.py`
- `tt/brain/hippocampus/recall.py`

### Vector search: cosine similarity
Memory retrieval uses **cosine similarity** (common default for embedding search).
Why cosine is usually the move:
- **Scale-invariant**: compares direction (semantic meaning) more than vector magnitude
- **Embedding-friendly**: many embedding pipelines are normalized or behave well under cosine distance
- **Stable ranking**: tends to give consistent “closest meaning” matches across varying text lengths

(Implementation detail depends on your pgvector query operator/settings, but the concept is “nearest neighbors by cosine distance”.)

---

## Concurrency (so it stays responsive)
TED keeps the session snappy by separating concerns into independent loops/threads:
- mic streaming loop
- WebSocket receive loop (events + tool calls)
- tool execution threads (so long-running tools don’t stall audio)

---

## Key files
- Realtime session: `tt/brain/prefrontal_cortex/openai_realtime.py`
- Tool plumbing: `tt/brain/handlers/tools_plug.py`, `tt/brain/handlers/websocket_tool_calls.py`
- Memory: `tt/brain/hippocampus/memorize.py`, `tt/brain/hippocampus/recall.py`
- Wake word: `tt/ears/wakeword_start.py`
