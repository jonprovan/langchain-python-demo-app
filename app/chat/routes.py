"""Routes for the chat page: runs a question through the Corrective RAG
graph and returns the answer, citations, and a LangSmith trace link."""

from flask import Blueprint, jsonify, render_template, request

from app.config import Config
from app.graph.build import get_graph
from app.kb_status import IN_PROGRESS_STATUSES, latest_ingestion_status
from app.langsmith_utils import run_with_trace_link

chat_bp = Blueprint("chat", __name__)


@chat_bp.route("/", methods=["GET"])
def chat_page():
    """Render the chat page. Shows a setup warning instead of the chat box
    if the Knowledge Base hasn't been provisioned yet, or an ingesting
    banner if the most recent upload hasn't finished indexing -- questions
    asked before that point would silently search a stale/incomplete
    index instead of erroring."""
    kb_configured = Config.kb_configured()
    ingesting = kb_configured and latest_ingestion_status() in IN_PROGRESS_STATUSES
    return render_template("chat.html", kb_configured=kb_configured, ingesting=ingesting)


@chat_bp.route("/ask", methods=["POST"])
def ask():
    """Run the user's question through the compiled Corrective RAG graph
    and return the answer, citations, and trace link as JSON for the page's
    fetch() call to render. Refuses to run while the most recent document
    is still ingesting, since retrieval would just miss it."""
    if not Config.kb_configured():
        return jsonify({"error": "Knowledge Base is not configured. Run scripts/provision_kb.py first."}), 400

    if latest_ingestion_status() in IN_PROGRESS_STATUSES:
        return jsonify({"error": "A document is still being ingested. Please wait for ingestion to finish before asking questions."}), 409

    question = request.json.get("question", "").strip()
    if not question:
        return jsonify({"error": "Question cannot be empty."}), 400

    graph = get_graph()
    inputs = {
        "question": question,
        "original_question": question,
        "documents": [],
        "grade": "",
        "rewrite_count": 0,
        "answer": "",
        "citations": [],
    }
    result, trace_url = run_with_trace_link(graph, inputs)

    return jsonify(
        {
            "answer": result["answer"],
            "citations": result["citations"],
            "trace_url": trace_url,
            "rewrite_count": result["rewrite_count"],
        }
    )
