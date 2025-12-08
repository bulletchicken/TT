import os

from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


# ElevenLabs Configuration
ELEVENLABS_AGENT_ID = os.getenv(
    "ELEVENLABS_AGENT_ID", "agent_3601kb6wrdbxe3ks35npx1dnsyk5"
)
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

# OpenAI Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Porcupine Configuration (for wake word detection)
PORCUPINE_ACCESS_KEY = os.getenv("PORCUPINE_ACCESS_KEY")

# Supabase Configuration
SUPABASE_URL: str = _require_env("SUPABASE_URL")
SUPABASE_KEY: str = _require_env("SUPABASE_KEY")
