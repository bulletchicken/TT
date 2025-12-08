from openai import OpenAI

from tt.config import OPENAI_API_KEY


def summarize(messages):
    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = (
        "Provide a concise summary of this conversation that "
        "captures key events and emotions. Here is the message:\n"
        f"{messages}"
    )

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
