from openai import OpenAI

from tt.config import OPENAI_API_KEY

client = OpenAI(api_key=OPENAI_API_KEY)


def embed(messages):
    response = client.embeddings.create(input=messages, model="text-embedding-3-small")

    # always outputs a list, but where you are then you decide to use [0] or use it as a list
    return [item.embedding for item in response.data]
