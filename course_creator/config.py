import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
OLLAMA_END_POINT = os.getenv("OLLAMA_END_POINT", "http://localhost:11434")

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
OPENAI_MODELS = parse_models("OPENAI_MODEL")
GOOGLE_MODELS = parse_models("GOOGLE_MODEL")
OLLAMA_MODELS = parse_models("OLLAMA_MODEL")

PROVIDER_CONFIG = {
    "Anthropic": {"api_key": ANTHROPIC_API_KEY, "models": ANTHROPIC_MODELS},
    # "OpenAI": {"api_key": OPENAI_API_KEY, "models": OPENAI_MODELS},
    # "Google": {"api_key": GOOGLE_API_KEY, "models": GOOGLE_MODELS},
    "Ollama": {"endpoint": OLLAMA_END_POINT, "models": OLLAMA_MODELS},
}
