"""Knowledge Base ingestion status helper.

Shared by the chat blueprint (to warn/block questions while a document is
still being chunked/embedded/indexed) and available for the documents
blueprint if it needs a general status check beyond polling one specific
job by ID.
"""

from app.bedrock_clients import get_bedrock_agent_client
from app.config import Config

# Ingestion jobs report one of these four statuses (per the Bedrock API).
IN_PROGRESS_STATUSES = ("STARTING", "IN_PROGRESS")


def latest_ingestion_status():
    """Return the status string of the most recently started ingestion job
    for this app's data source, or None if no ingestion job has ever run.

    Retrieval only searches documents that have already finished ingesting,
    so a question asked while the most recent upload is still processing
    will silently search a stale/incomplete index rather than error --
    this lets routes check first and tell the user to wait instead.
    """
    bedrock_agent = get_bedrock_agent_client()
    response = bedrock_agent.list_ingestion_jobs(
        knowledgeBaseId=Config.KNOWLEDGE_BASE_ID,
        dataSourceId=Config.DATA_SOURCE_ID,
        sortBy={"attribute": "STARTED_AT", "order": "DESCENDING"},
        maxResults=1,
    )
    jobs = response.get("ingestionJobSummaries", [])
    return jobs[0]["status"] if jobs else None
