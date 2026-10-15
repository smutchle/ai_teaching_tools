import os
from dotenv import load_dotenv

load_dotenv()

# VT ARC hosted LLMs, reached through ARC's Anthropic-compatible API.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_ENDPOINT = os.getenv("ANTHROPIC_ENDPOINT", "https://llm-api.arc.vt.edu/api/v1")

# Neo4j config (dedicated instance for this project)
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7688")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "course_creator")


def parse_models(env_var):
    models = os.getenv(env_var)
    if models:
        return [model.strip() for model in models.split(',')]
    return []


ANTHROPIC_MODELS = parse_models("ANTHROPIC_MODEL")

# VT ARC is the only provider.
PROVIDER_CONFIG = {
    "ARC": {
        "api_key": ANTHROPIC_API_KEY,
        "endpoint": ANTHROPIC_ENDPOINT,
        "models": ANTHROPIC_MODELS,
    },
}
