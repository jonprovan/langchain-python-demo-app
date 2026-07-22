"""Routes for uploading a document and ingesting it into the Knowledge Base."""

from flask import Blueprint, jsonify, render_template, request
from werkzeug.utils import secure_filename

from app.bedrock_clients import get_bedrock_agent_client, get_s3_client
from app.config import Config

documents_bp = Blueprint("documents", __name__)


@documents_bp.route("/upload", methods=["GET"])
def upload():
    """Render the upload page. Shows a setup warning instead of the form if
    the Knowledge Base hasn't been provisioned yet (see
    scripts/provision_kb.py), since uploading would just fail with a
    confusing boto3 error otherwise."""
    return render_template("upload.html", kb_configured=Config.kb_configured())


@documents_bp.route("/upload", methods=["POST"])
def do_upload():
    """Handle the upload form submission: push the file to the Knowledge
    Base's S3 data source bucket, then kick off an asynchronous ingestion
    job so Bedrock chunks/embeds/indexes it. Returns the ingestion job ID as
    JSON so the page's JS can poll /documents/status/<job_id> instead of
    blocking this request on ingestion (which can take tens of seconds)."""
    if not Config.kb_configured():
        return jsonify({"error": "Knowledge Base is not configured. Run scripts/provision_kb.py first."}), 400

    uploaded_file = request.files.get("file")
    if uploaded_file is None or uploaded_file.filename == "":
        return jsonify({"error": "No file selected."}), 400

    filename = secure_filename(uploaded_file.filename)

    s3 = get_s3_client()
    s3.put_object(Bucket=Config.KB_DATA_BUCKET, Key=filename, Body=uploaded_file.read())

    bedrock_agent = get_bedrock_agent_client()
    response = bedrock_agent.start_ingestion_job(
        knowledgeBaseId=Config.KNOWLEDGE_BASE_ID,
        dataSourceId=Config.DATA_SOURCE_ID,
    )
    job_id = response["ingestionJob"]["ingestionJobId"]

    return jsonify({"filename": filename, "ingestion_job_id": job_id})


@documents_bp.route("/status/<job_id>", methods=["GET"])
def status(job_id):
    """Return the current status of an ingestion job so the upload page can
    poll until it reaches COMPLETE (or report FAILED) before letting the
    user move on to the chat page."""
    bedrock_agent = get_bedrock_agent_client()
    response = bedrock_agent.get_ingestion_job(
        knowledgeBaseId=Config.KNOWLEDGE_BASE_ID,
        dataSourceId=Config.DATA_SOURCE_ID,
        ingestionJobId=job_id,
    )
    return jsonify({"status": response["ingestionJob"]["status"]})
