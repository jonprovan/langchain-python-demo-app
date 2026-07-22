# Helper for running the graph with a shareable LangSmith trace link.
#
# Kept separate from the graph and routes so both stay focused on RAG logic --
# this module's only job is wiring up run IDs and turning them into a public
# "View trace" URL for the chat UI.

import uuid

from langchain_core.tracers.langchain import wait_for_all_tracers
from langsmith import Client


# Invoke the compiled graph with an explicit run_id, then ask LangSmith
# for a public share URL for that run.
#
# Returns (result, trace_url). trace_url is None if LangSmith tracing
# isn't configured (e.g. LANGSMITH_API_KEY unset) or the share call fails
# -- the chat UI treats that as "no trace link available" rather than an
# error, so the demo still works with tracing off.
def run_with_trace_link(graph, inputs):
    run_id = str(uuid.uuid4())
    result = graph.invoke(
        inputs, config={"run_id": run_id, "run_name": "rag-query"}
    )

    # LangChain posts run traces to LangSmith asynchronously via a
    # background thread, so the run may not exist server-side yet the
    # instant graph.invoke() returns. Block until that queue drains before
    # asking LangSmith to share a run it might not have received.
    wait_for_all_tracers()

    trace_url = None
    try:
        trace_url = Client().share_run(run_id)
    except Exception:
        pass

    return result, trace_url
