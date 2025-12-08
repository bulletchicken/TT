from openai import OpenAI

from tt.config import OPENAI_API_KEY

client = OpenAI(api_key=OPENAI_API_KEY)


def embed(messages):
    response = client.embeddings.create(input=messages, model="text-embedding-3-small")
    return [item.embedding for item in response.data]
