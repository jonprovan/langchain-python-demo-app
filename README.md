# langchain-python-demo-app

A training/demo Flask app showing LangChain Core (LCEL), LangGraph, LangSmith,
and AWS Bedrock working together in one real, runnable application: a
Corrective RAG chatbot backed by an Amazon Bedrock Knowledge Base.

Presenting this to a class? See [WALKTHROUGH.md](WALKTHROUGH.md) for a
facilitator's script (code tour, live demo steps, discussion prompts, and
troubleshooting).

## What this demonstrates

- **LCEL** (`prompt | model | parser`) inside each graph node -- the small,
  composable chain layer.
- **LangGraph** (`app/graph/build.py`) -- the control-flow layer on top of
  LCEL: a retrieve -> grade -> generate graph with a bounded rewrite/retry
  loop, which is the part LCEL alone can't express cleanly.
- **LangSmith** -- every chat request is traced and exposed as a public
  "View trace" link (`app/langsmith_utils.py`), no LangSmith login needed to
  view it.
- **AWS Bedrock** -- both a Knowledge Base (managed RAG: chunking,
  embedding, retrieval via OpenSearch Serverless) and `ChatBedrockConverse`
  for generation/grading/rewriting, all under a single IAM credential chain.

## Architecture

```
retrieve -> grade_documents -> [relevant] -> generate -> END
                             -> [irrelevant, retries left] -> rewrite_query -> retrieve (loop)
                             -> [irrelevant, retries exhausted] -> generate (low-confidence) -> END
```

See `app/graph/state.py`, `app/graph/nodes.py`, and `app/graph/build.py` for
the implementation, and `scripts/provision_kb.py` for the AWS Knowledge Base
infrastructure it depends on.

## Setup

1. Create a virtual environment and install dependencies:
   ```
   python -m venv .venv
   .venv\Scripts\activate   # Windows
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and fill in your AWS region and (optionally)
   LangSmith API key. AWS credentials come from your normal IAM credential
   chain (env vars, shared config file, SSO profile, or role) -- set
   `AWS_PROFILE` in `.env` if you use a named profile.
3. Provision the Bedrock Knowledge Base infrastructure (see below), then add
   the printed `KNOWLEDGE_BASE_ID`, `DATA_SOURCE_ID`, and `KB_DATA_BUCKET`
   values to `.env`.

## Provisioning the Knowledge Base

`scripts/provision_kb.py` creates an S3 bucket, an OpenSearch Serverless
VECTORSEARCH collection + vector index, an IAM role, a Bedrock Knowledge
Base, and an S3 data source. **It is never run automatically** -- run it
yourself, with your own AWS credentials, when you're ready:

```
python scripts/provision_kb.py --yes
```

**Cost warning:** OpenSearch Serverless VECTORSEARCH collections are
billable. Roughly $10-20/month if your account has the newer scale-to-zero
("NextGen") behavior, otherwise the collection runs continuously at roughly
$350/month. The script prints this warning and requires confirmation
(`--yes` or an interactive prompt) before creating the collection.

When you're done with the demo, tear everything down:

```
python scripts/provision_kb.py --teardown --yes
```

**Non-production simplification:** the OpenSearch Serverless network
security policy created by this script uses public network access, for demo
simplicity. A production setup would use a VPC endpoint instead.

## Running the app

```
flask --app wsgi run
```

Then:

1. Open the **Upload** page and upload a small text file containing a fact
   an LLM couldn't already know (e.g. a made-up policy number) -- this
   proves retrieval is actually driving the answer.
2. Wait for ingestion to reach "complete" (the page polls automatically).
3. Open the **Chat** page and ask a question only that document can answer.
   The answer should cite the fact, list the source chunk under Citations,
   and show a "View trace" link to the LangSmith run.
4. Try an unrelated question to see the correction loop: the trace should
   show `grade_documents` returning "irrelevant", a `rewrite_query` step,
   and a second retrieval before the graph terminates.

## Package versions

Pinned in `requirements.txt` after installation via `pip freeze`. If you
bump dependencies, re-verify `langchain-aws`, `langgraph`, and `langsmith`
compatibility -- these are fast-moving packages.
