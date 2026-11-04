"""Export validated server configuration for gcloud's --env-vars-file."""

import json

from app import config


def deployment_environment() -> dict[str, str]:
    names = (
        "OPENROUTER_BASE", "LLM_MODEL", "EMBED_MODEL", "EMBED_DIM",
        "RERANK_ENABLED", "RERANK_MODEL", "RERANK_TIMEOUT_SECONDS",
        "RERANK_TEXT_CANDIDATES", "RERANK_IMAGE_TOP_K", "RERANK_FINAL_TOP_K",
    )
    values = {name: str(getattr(config, name)) for name in names}
    values["RERANK_ENABLED"] = str(config.RERANK_ENABLED).lower()
    values["OPENROUTER_API_KEY"] = config.get_openrouter_api_key()
    return values


if __name__ == "__main__":
    print(json.dumps(deployment_environment()))
