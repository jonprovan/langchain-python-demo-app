# Knowledge Base readiness check.
#
# Shared by the chat blueprint (to warn/block questions while the index isn't
# safely queryable yet) and available for the documents blueprint if it needs
# a general status check beyond polling one specific job by ID.

import datetime

from app.bedrock_clients import get_bedrock_agent_client
from app.config import Config

# Ingestion jobs report one of these four statuses (per the Bedrock API).
IN_PROGRESS_STATUSES = ("STARTING", "IN_PROGRESS")

# Empirically observed: OpenSearch Serverless's near-real-time indexing
# delay means a job can report COMPLETE a few seconds to under a minute
# before its vectors are actually searchable. There's no separate status
# to poll for "actually queryable now," so this is a fixed grace period
# rather than something we can detect precisely.
SETTLE_SECONDS = 45


def _seconds_since(timestamp):
    return (datetime.datetime.now(datetime.timezone.utc) - timestamp).total_seconds()


# Return False if the most recently started ingestion job is still
# running, or completed too recently for OpenSearch to have caught up,
# True otherwise (including when no ingestion job has ever run).
#
# Both cases matter: asking a question while a job is STARTING/
# IN_PROGRESS would search a stale index outright, and asking right after
# one reports COMPLETE can still miss the change because Bedrock
# reporting the job done doesn't mean OpenSearch Serverless has finished
# indexing it yet.
def kb_is_ready():
    bedrock_agent = get_bedrock_agent_client()
    response = bedrock_agent.list_ingestion_jobs(
        knowledgeBaseId=Config.KNOWLEDGE_BASE_ID,
        dataSourceId=Config.DATA_SOURCE_ID,
        sortBy={"attribute": "STARTED_AT", "order": "DESCENDING"},
        maxResults=1,
    )
    jobs = response.get("ingestionJobSummaries", [])
    if not jobs:
        return True

    job = jobs[0]
    if job["status"] in IN_PROGRESS_STATUSES:
        return False
    if job["status"] == "COMPLETE":
        return _seconds_since(job["updatedAt"]) >= SETTLE_SECONDS
    # FAILED (or any other terminal status): nothing to wait on.
    return True
