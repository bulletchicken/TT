from openai import OpenAI

from tt.brain.temporal_lobe.prompts.summarize_conversation import SUMMARIZE_PROMPT
from tt.config import OPENAI_API_KEY


def summarize(messages):
    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = f"{SUMMARIZE_PROMPT}{messages}"

    response = client.responses.create(
        model="gpt-4o-mini",
        input=[
            {
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
            }
        ],
        temperature=0.8,
    )
    return response.output_text
