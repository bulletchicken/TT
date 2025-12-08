from tt.brain.hippocampus.utils.embed import embed
from tt.brain.hippocampus.utils.store import store

from .utils.summarize import summarize


def memorize(model, duration, detailed_conversation, session_start, session_end):
    summary = summarize(detailed_conversation)
    print(summary)
    embedding = embed(summary)
    store(
        model,
        duration,
        summary,
        detailed_conversation,
        embedding,
        session_start,
        session_end,
    )


if __name__ == "__main__":
    memorize(
        "gpt-4o-mini",
        10,
        """Hey Ted, I'm feeling pretty sad today because I lost my league game.
        Oh no, let me dance to cheer you up
        Haha, thanks Ted. I really do like seeing you dance.""",
        "2023-01-01",
        "2023-01-02",
    )
