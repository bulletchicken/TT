import json
from pickle import HIGHEST_PROTOCOL

from openai import OpenAI
from pydantic import BaseModel

from tt.config import OPENAI_API_KEY


class highlight_output_format(BaseModel):
    memories: list[str]


def highlight(messages):
    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = (
        "Extract concise highlights and important things to remember from the conversation mainly about the user. "
        "A highlight should be specific "
        "Each highlight should capture the key moment including the mood/tone "
        "Try to avoid quoting redundant and common parts of the conversation unless it's apart of the highlight"
        "expressed when it was said."
        f"{messages}"
    )

    response = client.responses.parse(
        model="gpt-4o-mini",
        input=[
            {
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
            }
        ],
        text_format=highlight_output_format,
        temperature=0.7,
    )

    return response.output_parsed.memories
