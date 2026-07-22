# Wires the Corrective RAG nodes into a compiled LangGraph graph.
#
# Flow: retrieve -> grade_documents -> (generate | rewrite_query -> retrieve),
# looping until relevant or MAX_REWRITES is hit, then always ending at
# generate -> END.

from langgraph.graph import END, START, StateGraph

from app.graph.nodes import generate, grade_documents, retrieve, rewrite_query, route_after_grading
from app.graph.state import GraphState

_compiled_graph = None


# Assemble and compile the StateGraph from the node functions in
# nodes.py. Kept separate from get_graph() so tests/scripts can build a
# fresh, uncached graph if needed (e.g. before calling draw_mermaid_png()).
def build_graph():
    graph = StateGraph(GraphState)

    graph.add_node("retrieve", retrieve)
    graph.add_node("grade_documents", grade_documents)
    graph.add_node("rewrite_query", rewrite_query)
    graph.add_node("generate", generate)

    graph.add_edge(START, "retrieve")
    graph.add_edge("retrieve", "grade_documents")
    graph.add_conditional_edges(
        "grade_documents",
        route_after_grading,
        {"generate": "generate", "rewrite_query": "rewrite_query"},
    )
    graph.add_edge("rewrite_query", "retrieve")
    graph.add_edge("generate", END)

    return graph.compile()


# Return a process-wide singleton compiled graph, building it on first
# call. Compiling is cheap but there's no reason to redo it on every
# request, so routes.py should always go through this function rather
# than calling build_graph() directly.
def get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph
