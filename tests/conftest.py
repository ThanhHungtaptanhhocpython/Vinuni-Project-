import os

# Keep edge/shared tests free of LangSmith/Langfuse background threads.
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
os.environ.setdefault("LANGSMITH_TRACING", "false")
os.environ.setdefault("LANGFUSE_ENABLED", "false")
