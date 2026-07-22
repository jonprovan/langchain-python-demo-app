# Central place for reading environment configuration used across the app.

import os


# Typed accessor for the environment variables the app depends on.
#
# Values are read from process environment (populated by python-dotenv from
# .env in development). Grouped in one class so every other module has a
# single import instead of scattering os.environ calls everywhere.
class Config:
    AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

    # Bedrock model used for generation, document grading, and query rewriting
    # in the LangGraph nodes (app/graph/nodes.py).
    BEDROCK_MODEL_ID = os.environ.get(
        "BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    )

    # Must match the knn_vector dimension configured on the OpenSearch index
    # by scripts/provision_kb.py (Titan Text Embeddings V2 = 1024).
    EMBEDDING_MODEL_DIMENSION = int(os.environ.get("EMBEDDING_MODEL_DIMENSION", "1024"))

    KNOWLEDGE_BASE_ID = os.environ.get("KNOWLEDGE_BASE_ID", "")
    DATA_SOURCE_ID = os.environ.get("DATA_SOURCE_ID", "")
    KB_DATA_BUCKET = os.environ.get("KB_DATA_BUCKET", "")

    LANGSMITH_TRACING = os.environ.get("LANGSMITH_TRACING", "false")
    LANGSMITH_API_KEY = os.environ.get("LANGSMITH_API_KEY", "")
    LANGSMITH_PROJECT = os.environ.get("LANGSMITH_PROJECT", "langchain-demo-app")

    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")

    # Return True once the Knowledge Base has been provisioned and its
    # IDs are present in the environment. Routes use this to show a helpful
    # setup message instead of a raw Bedrock error when the KB isn't ready
    # yet.
    @classmethod
    def kb_configured(cls):
        return bool(cls.KNOWLEDGE_BASE_ID and cls.DATA_SOURCE_ID and cls.KB_DATA_BUCKET)
