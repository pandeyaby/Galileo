"""AWS Bedrock Converse → GalileoLogger (real boto3, no mock).

Usage:
  pip install boto3 galileo
  export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_REGION=us-east-1
  export GALILEO_API_KEY=... GALILEO_PROJECT=... GALILEO_LOG_STREAM=bedrock-integration
  export BEDROCK_MODEL_ID=amazon.nova-lite-v1:0   # or another Converse-capable model
  python examples/integrations/bedrock_galileo.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from _common import project_stream, require_galileo


def main() -> int:
    err = require_galileo()
    if err:
        return err
    # Accept explicit env OR the default boto3 credential chain (~/.aws, IAM role, etc.)
    has_explicit = bool(os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE"))
    if not has_explicit:
        try:
            import boto3

            sess = boto3.Session()
            if sess.get_credentials() is None:
                print(
                    "ERROR: AWS credentials required "
                    "(AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, AWS_PROFILE, or default chain). No mock."
                )
                return 2
        except ImportError:
            print("ERROR: install boto3: pip install boto3")
            return 2
        except Exception as exc:
            print(f"ERROR: AWS credential resolution failed: {type(exc).__name__}: {exc}")
            return 2

    try:
        import boto3
    except ImportError:
        print("ERROR: install boto3: pip install boto3")
        return 2

    project, stream = project_stream("bedrock-integration")
    model_id = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0")
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    query = "In one short paragraph: what is gradient checkpointing?"

    client = boto3.client("bedrock-runtime", region_name=region)
    from galileo import GalileoLogger

    logger = GalileoLogger(project=project, log_stream=stream)
    logger.start_trace(
        input=query,
        name="bedrock-galileo",
        tags=["integration", "bedrock", "dizzygraph-starter"],
        metadata={"framework": "bedrock-converse", "model_id": model_id, "region": region},
    )

    try:
        resp = client.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": query}]}],
            inferenceConfig={"maxTokens": 256, "temperature": 0.2},
        )
    except Exception as exc:
        print(f"ERROR: Bedrock converse failed: {type(exc).__name__}: {exc}")
        return 2

    parts = (((resp or {}).get("output") or {}).get("message") or {}).get("content") or []
    texts = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")]
    output = "\n".join(texts).strip()
    usage = (resp or {}).get("usage") or {}

    logger.add_llm_span(
        input=query,
        output=output,
        model=model_id,
        name="bedrock.converse",
        metadata={
            "otel.span_name": "bedrock.converse",
            "openinference.span.kind": "LLM",
            "input_tokens": str(usage.get("inputTokens", "")),
            "output_tokens": str(usage.get("outputTokens", "")),
        },
    )
    logger.conclude(output=output)
    logger.flush()
    print(f"galileo: {project}/{stream}")
    print("── answer ──")
    print(output[:800] or "(empty Bedrock response)")
    return 0 if output else 1


if __name__ == "__main__":
    raise SystemExit(main())
