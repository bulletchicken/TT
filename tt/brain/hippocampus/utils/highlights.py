import json
from pickle import HIGHEST_PROTOCOL

from openai import OpenAI
from pydantic import BaseModel

from tt.brain.temporal_lobe.prompts.extract_highlights import HIGHLIGHT_PROMPT
from tt.config import OPENAI_API_KEY


class highlight_output_format(BaseModel):
    memories: list[str]


def highlight(messages):
    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = f"{HIGHLIGHT_PROMPT}{messages}"

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
