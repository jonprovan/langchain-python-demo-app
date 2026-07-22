# boto3 client builders for every AWS service the app talks to.
#
# All clients here use the default AWS IAM credential chain (env vars, shared
# credentials file, SSO profile, or role) -- there is deliberately no separate
# bearer-token path for chat vs. retrieval. Bedrock API keys only cover
# bedrock-runtime (InvokeModel/Converse), not bedrock-agent-runtime
# (Retrieve/RetrieveAndGenerate), so a single IAM identity keeps both call
# paths working with one credential setup.

import boto3

from app.config import Config


# Build the client used for chat/generation calls
# (ChatBedrockConverse in app/graph/nodes.py).
def get_bedrock_runtime_client():
    return boto3.client("bedrock-runtime", region_name=Config.AWS_REGION)


# Build the client used for Knowledge Base retrieval calls
# (AmazonKnowledgeBasesRetriever in app/graph/nodes.py).
def get_bedrock_agent_runtime_client():
    return boto3.client("bedrock-agent-runtime", region_name=Config.AWS_REGION)


# Build the client used for Knowledge Base management calls
# (start_ingestion_job/get_ingestion_job in app/documents/routes.py).
def get_bedrock_agent_client():
    return boto3.client("bedrock-agent", region_name=Config.AWS_REGION)


# Build the client used to upload documents to the Knowledge Base's
# S3 data source bucket.
def get_s3_client():
    return boto3.client("s3", region_name=Config.AWS_REGION)
