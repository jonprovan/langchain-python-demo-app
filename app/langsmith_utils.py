"""Helper for running the graph with a shareable LangSmith trace link.

Kept separate from the graph and routes so both stay focused on RAG logic --
this module's only job is wiring up run IDs and turning them into a public
"View trace" URL for the chat UI.
"""

import uuid

from langsmith import Client


def run_with_trace_link(graph, inputs):
    """Invoke the compiled graph with an explicit run_id, then ask LangSmith
    for a public share URL for that run.

    Returns (result, trace_url). trace_url is None if LangSmith tracing
    isn't configured (e.g. LANGSMITH_API_KEY unset) or the share call fails
    -- the chat UI treats that as "no trace link available" rather than an
    error, so the demo still works with tracing off.
    """
    run_id = str(uuid.uuid4())
    result = graph.invoke(
        inputs, config={"run_id": run_id, "run_name": "rag-query"}
    )

    trace_url = None
    try:
        trace_url = Client().share_run(run_id)
    except Exception:
        pass

    return result, trace_url
