"""Shared state definition for the Corrective RAG LangGraph graph."""

from typing import TypedDict


class GraphState(TypedDict):
    """The single object passed between every node in the graph. Each node
    reads whatever fields it needs and returns a dict of the fields it wants
    to update -- LangGraph merges that into this state before calling the
    next node.
    """

    # Current retrieval query. Starts equal to original_question, but
    # rewrite_query can replace it on a retry loop iteration.
    question: str

    # The user's original question, kept unmodified so generate() can always
    # answer the question actually asked, even after query rewrites.
    original_question: str

    # Raw text chunks returned by the most recent retrieve() call.
    documents: list

    # "relevant" or "irrelevant", set by grade_documents(). Drives the
    # conditional edge that decides whether to generate or rewrite-and-retry.
    grade: str

    # How many times rewrite_query -> retrieve has looped so far. Bounded to
    # guarantee the graph terminates instead of looping forever on a
    # question the Knowledge Base genuinely can't answer.
    rewrite_count: int

    # Final answer text produced by generate().
    answer: str

    # Source citations (e.g. S3 key / chunk metadata) extracted from the
    # retrieved documents, shown alongside the answer in the chat UI.
    citations: list
