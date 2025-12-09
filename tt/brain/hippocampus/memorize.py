from tt.brain.hippocampus.utils.embed import embed
from tt.brain.hippocampus.utils.highlights import highlight
from tt.brain.hippocampus.utils.store import store

from .utils.summarize import summarize


def create_highlights(detailed_conversation):
    import time

    t0 = time.perf_counter()
    highlights = highlight(detailed_conversation)
    t1 = time.perf_counter()
    print(f"Highlights generation ({t1 - t0:.2f}s): {len(highlights)} items")

    embeddings = embed(highlights)
    t2 = time.perf_counter()
    print(f"Highlights embedding ({t2 - t1:.2f}s)")

    return list(zip(highlights, embeddings))


def memorize(model, duration, detailed_conversation, session_start, session_end):
    import time

    t0 = time.perf_counter()
    summary = summarize(detailed_conversation)
    t1 = time.perf_counter()
    print(f"Summary ({t1 - t0:.2f}s): {summary}")

    embedding = embed(summary)[0]
    t2 = time.perf_counter()
    print(f"Summary embedding ({t2 - t1:.2f}s)")

    highlights_and_embeddings = create_highlights(detailed_conversation)
    t3 = time.perf_counter()

    # Convert duration to int seconds
    if hasattr(duration, "total_seconds"):
        duration = int(duration.total_seconds())
    else:
        duration = int(duration)

    # Convert datetime objects to ISO strings
    if hasattr(session_start, "isoformat"):
        session_start = session_start.isoformat()
    if hasattr(session_end, "isoformat"):
        session_end = session_end.isoformat()

    store(
        model,
        duration,
        summary,
        detailed_conversation,
        embedding,
        session_start,
        session_end,
        highlights_and_embeddings,
    )
    t4 = time.perf_counter()
    print(f"Store ({t4 - t3:.2f}s)")
    print("Memorization completed")


if __name__ == "__main__":
    memorize(
        "gpt-4o-mini",
        10,
        """Hey TED, I need a plan to organize my desk before midterms.
        Start by throwing away obvious trash, then sort papers into keep/shred piles.
        Should I keep my old notebooks? Keep one for reference, store the rest in your red box.""",
        "2023-01-01",
        "2023-01-02",
    )
