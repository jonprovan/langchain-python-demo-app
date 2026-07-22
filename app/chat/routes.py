# Routes for the chat page: runs a question through the Corrective RAG
# graph and returns the answer, citations, and a LangSmith trace link.

from flask import Blueprint, jsonify, render_template, request

from app.config import Config
from app.graph.build import get_graph
from app.kb_status import kb_is_ready
from app.langsmith_utils import run_with_trace_link

chat_bp = Blueprint("chat", __name__)


# Render the chat page. Shows a setup warning instead of the chat box
# if the Knowledge Base hasn't been provisioned yet, or a not-ready
# banner if the most recent upload/delete hasn't finished settling --
# questions asked before that point would silently search a stale index
# instead of erroring.
@chat_bp.route("/", methods=["GET"])
def chat_page():
    kb_configured = Config.kb_configured()
    not_ready = kb_configured and not kb_is_ready()
    return render_template("chat.html", kb_configured=kb_configured, not_ready=not_ready)


# Run the user's question through the compiled Corrective RAG graph
# and return the answer, citations, and trace link as JSON for the page's
# fetch() call to render. Refuses to run until the most recent
# ingestion job has finished and settled, since retrieval could
# otherwise miss the change entirely.
@chat_bp.route("/ask", methods=["POST"])
def ask():
    if not Config.kb_configured():
        return jsonify({"error": "Knowledge Base is not configured. Run scripts/provision_kb.py first."}), 400

    if not kb_is_ready():
        return jsonify({"error": "The Knowledge Base is still indexing a recent change. Please wait a bit before asking questions."}), 409

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
