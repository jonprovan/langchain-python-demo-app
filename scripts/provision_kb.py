#!/usr/bin/env python
"""One-time infrastructure provisioning for the Bedrock Knowledge Base.

Creates an S3 bucket, an OpenSearch Serverless VECTORSEARCH collection +
vector index, an IAM role for Bedrock, a Knowledge Base, and an S3 data
source, then kicks off the first ingestion job.

This script is NEVER run automatically by the Flask app -- it costs real
money (see the warning printed below) and creates real AWS resources, so a
human has to opt in explicitly with their own credentials.

Usage:
    python scripts/provision_kb.py --yes          # provision everything
    python scripts/provision_kb.py --teardown --yes   # delete everything

Every "create_*" function checks whether its resource already exists before
creating it, so re-running the script after a partial failure just picks up
where it left off instead of erroring on duplicates.
"""

import argparse
import json
import sys
import time

import boto3
from botocore.exceptions import ClientError
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection

# All resource names are derived from this single prefix so the whole stack
# can be identified/torn down as a unit.
NAME_PREFIX = "langchain-demo"
BUCKET_NAME_TEMPLATE = "{prefix}-kb-docs-{account_id}"
COLLECTION_NAME = f"{NAME_PREFIX}-kb"
INDEX_NAME = f"{NAME_PREFIX}-index"
ROLE_NAME = f"{NAME_PREFIX}-kb-role"
KB_NAME = f"{NAME_PREFIX}-kb"
DATA_SOURCE_NAME = f"{NAME_PREFIX}-s3-source"

# Titan Text Embeddings V2. Pinned here and reused everywhere a dimension is
# needed (the vector index and the Knowledge Base config) because a mismatch
# between the two is the single most common way this provisioning fails.
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIMENSION = 1024

# Vector/text/metadata field names used by both the OpenSearch index (step 6)
# and the Knowledge Base's fieldMapping (step 7) -- they must match exactly.
VECTOR_FIELD = "vector"
TEXT_FIELD = "text"
METADATA_FIELD = "metadata"

COST_WARNING = """
WARNING: This will create a billable OpenSearch Serverless VECTORSEARCH
collection. Cost depends on your account:
  - Accounts with OpenSearch Serverless "NextGen" scale-to-zero behavior:
    roughly $10-20/month.
  - Accounts without it: the collection runs continuously, roughly
    $350/month.
This is NOT reversible cost-wise once OCUs start accruing for the billing
period. Use --teardown when you're done with the demo to remove it.
"""


def _clients(region):
    """Build every boto3/opensearch-py client this script needs, all via
    the caller's own default IAM credential chain (profile/SSO/role) -- no
    separate credentials are created or required."""
    session = boto3.Session(region_name=region)
    return {
        "s3": session.client("s3"),
        "iam": session.client("iam"),
        "sts": session.client("sts"),
        "aoss": session.client("opensearchserverless"),
        "bedrock_agent": session.client("bedrock-agent"),
        "session": session,
    }


def _account_id(clients):
    """Look up the caller's AWS account ID, used to build a globally-unique
    bucket name and to scope the IAM trust policy's SourceAccount condition."""
    return clients["sts"].get_caller_identity()["Account"]


def create_bucket(clients, bucket_name, region):
    """Step 1: create the S3 bucket used as the Knowledge Base's data
    source, with public access blocked. No-ops if the bucket already exists
    and is owned by this account."""
    s3 = clients["s3"]
    try:
        s3.head_bucket(Bucket=bucket_name)
        print(f"[1/9] Bucket {bucket_name} already exists, skipping.")
        return
    except ClientError:
        pass

    print(f"[1/9] Creating bucket {bucket_name}...")
    if region == "us-east-1":
        s3.create_bucket(Bucket=bucket_name)
    else:
        s3.create_bucket(
            Bucket=bucket_name,
            CreateBucketConfiguration={"LocationConstraint": region},
        )
    s3.put_public_access_block(
        Bucket=bucket_name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )


def _policy_exists(aoss, name, policy_type):
    """Return True if an OpenSearch Serverless security policy with this
    name/type already exists (used to make create_security_policies
    idempotent)."""
    try:
        aoss.get_security_policy(name=name, type=policy_type)
        return True
    except ClientError:
        return False


def create_security_policies(clients, collection_name):
    """Step 2: create the OpenSearch Serverless encryption and network
    security policies for the collection.

    The network policy uses public network access for demo simplicity --
    a deliberate non-production simplification, called out in the README.
    """
    aoss = clients["aoss"]

    encryption_name = f"{collection_name}-enc"
    if not _policy_exists(aoss, encryption_name, "encryption"):
        print("[2/9] Creating encryption security policy...")
        aoss.create_security_policy(
            name=encryption_name,
            type="encryption",
            policy=json.dumps(
                {
                    "Rules": [
                        {"ResourceType": "collection", "Resource": [f"collection/{collection_name}"]}
                    ],
                    "AWSOwnedKey": True,
                }
            ),
        )
    else:
        print("[2/9] Encryption security policy already exists, skipping.")

    network_name = f"{collection_name}-net"
    if not _policy_exists(aoss, network_name, "network"):
        print("[2/9] Creating network security policy (public access, demo simplification)...")
        aoss.create_security_policy(
            name=network_name,
            type="network",
            policy=json.dumps(
                [
                    {
                        "Rules": [
                            {"ResourceType": "collection", "Resource": [f"collection/{collection_name}"]},
                            {"ResourceType": "dashboard", "Resource": [f"collection/{collection_name}"]},
                        ],
                        "AllowFromPublic": True,
                    }
                ]
            ),
        )
    else:
        print("[2/9] Network security policy already exists, skipping.")


def create_access_policy(clients, collection_name, account_id, region, role_name):
    """Step 3: create the OpenSearch Serverless *data access* policy.

    This is the step most commonly missed: IAM permissions alone do NOT
    grant AOSS data-plane access. Both the caller's own IAM principal (so
    this script can create the vector index in step 6) and the Bedrock KB
    service role (so retrieval/ingestion work) need explicit grants here.
    The role ARN is deterministic from account + name, so we can reference
    it before create_iam_role actually creates the role.
    """
    aoss = clients["aoss"]
    sts = clients["sts"]
    caller_arn = sts.get_caller_identity()["Arn"]
    role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"

    access_name = f"{collection_name}-access"
    try:
        aoss.get_access_policy(name=access_name, type="data")
        print("[3/9] Data access policy already exists, skipping.")
        return
    except ClientError:
        pass

    print("[3/9] Creating data access policy...")
    aoss.create_access_policy(
        name=access_name,
        type="data",
        policy=json.dumps(
            [
                {
                    "Rules": [
                        {
                            "ResourceType": "index",
                            "Resource": [f"index/{collection_name}/*"],
                            "Permission": [
                                "aoss:CreateIndex",
                                "aoss:DescribeIndex",
                                "aoss:ReadDocument",
                                "aoss:WriteDocument",
                                "aoss:UpdateIndex",
                                "aoss:DeleteIndex",
                            ],
                        },
                        {
                            "ResourceType": "collection",
                            "Resource": [f"collection/{collection_name}"],
                            "Permission": ["aoss:CreateCollectionItems", "aoss:DescribeCollectionItems"],
                        },
                    ],
                    "Principal": [caller_arn, role_arn],
                }
            ]
        ),
    )


def create_collection(clients, collection_name):
    """Step 4: create the VECTORSEARCH collection and poll until it's
    ACTIVE. Returns (collection_id, collection_arn)."""
    aoss = clients["aoss"]

    existing = aoss.list_collections(collectionFilters={"name": collection_name})["collectionSummaries"]
    if existing:
        collection_id = existing[0]["id"]
        print(f"[4/9] Collection {collection_name} already exists ({collection_id}).")
    else:
        print(f"[4/9] Creating collection {collection_name}...")
        response = aoss.create_collection(name=collection_name, type="VECTORSEARCH")
        collection_id = response["createCollectionDetail"]["id"]

    print("[4/9] Waiting for collection to become ACTIVE...")
    while True:
        details = aoss.batch_get_collection(ids=[collection_id])["collectionDetails"][0]
        if details["status"] == "ACTIVE":
            return collection_id, details["arn"]
        if details["status"] == "FAILED":
            raise RuntimeError(f"Collection {collection_name} failed to create.")
        time.sleep(10)


def create_iam_role(clients, role_name, account_id, region, bucket_name, collection_arn):
    """Step 5: create the IAM role Bedrock assumes to read S3, call the
    embedding model, and read/write the OpenSearch collection. Trust policy
    is scoped to this account/region's Knowledge Base ARNs via
    SourceAccount/SourceArn, per AWS's documented confused-deputy
    mitigation."""
    iam = clients["iam"]

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock:{region}:{account_id}:knowledge-base/*"},
                },
            }
        ],
    }

    try:
        iam.get_role(RoleName=role_name)
        print(f"[5/9] Role {role_name} already exists, skipping create.")
    except ClientError:
        print(f"[5/9] Creating role {role_name}...")
        iam.create_role(RoleName=role_name, AssumeRolePolicyDocument=json.dumps(trust_policy))

    permissions_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": "bedrock:InvokeModel",
                "Resource": f"arn:aws:bedrock:{region}::foundation-model/{EMBEDDING_MODEL_ID}",
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{bucket_name}", f"arn:aws:s3:::{bucket_name}/*"],
            },
            {
                "Effect": "Allow",
                "Action": "aoss:APIAccessAll",
                "Resource": collection_arn,
            },
        ],
    }
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=f"{role_name}-permissions",
        PolicyDocument=json.dumps(permissions_policy),
    )

    role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
    # New IAM roles need a few seconds to propagate before Bedrock can
    # assume them reliably.
    time.sleep(10)
    return role_arn


def create_vector_index(clients, collection_id, region):
    """Step 6: create the OpenSearch *data-plane* vector index via
    opensearch-py, signed with the "aoss" service name (not "es" -- that's
    the #1 cause of signature errors here). Must exist before
    create_knowledge_base."""
    credentials = clients["session"].get_credentials()
    auth = AWSV4SignerAuth(credentials, region, "aoss")
    host = f"{collection_id}.{region}.aoss.amazonaws.com"

    client = OpenSearch(
        hosts=[{"host": host, "port": 443}],
        http_auth=auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=30,
    )

    if client.indices.exists(index=INDEX_NAME):
        print(f"[6/9] Index {INDEX_NAME} already exists, skipping.")
        return

    print(f"[6/9] Creating vector index {INDEX_NAME}...")
    body = {
        "settings": {"index": {"knn": True}},
        "mappings": {
            "properties": {
                VECTOR_FIELD: {
                    "type": "knn_vector",
                    "dimension": EMBEDDING_DIMENSION,
                    "method": {"name": "hnsw", "engine": "faiss", "space_type": "l2"},
                },
                TEXT_FIELD: {"type": "text"},
                METADATA_FIELD: {"type": "text", "index": False},
            }
        },
    }

    # The collection can report ACTIVE slightly before the data plane is
    # ready to accept index creation; retry for up to ~2 minutes.
    for attempt in range(12):
        try:
            client.indices.create(index=INDEX_NAME, body=body)
            return
        except Exception:
            if attempt == 11:
                raise
            time.sleep(10)


def create_knowledge_base(clients, role_arn, collection_arn, region):
    """Step 7: create the Bedrock Knowledge Base backed by the OpenSearch
    Serverless index, and poll until ACTIVE. Returns the knowledge_base_id."""
    bedrock_agent = clients["bedrock_agent"]

    existing = [
        kb for kb in bedrock_agent.list_knowledge_bases()["knowledgeBaseSummaries"]
        if kb["name"] == KB_NAME
    ]
    if existing:
        kb_id = existing[0]["knowledgeBaseId"]
        print(f"[7/9] Knowledge Base {KB_NAME} already exists ({kb_id}).")
    else:
        print(f"[7/9] Creating Knowledge Base {KB_NAME}...")
        response = bedrock_agent.create_knowledge_base(
            name=KB_NAME,
            roleArn=role_arn,
            knowledgeBaseConfiguration={
                "type": "VECTOR",
                "vectorKnowledgeBaseConfiguration": {
                    "embeddingModelArn": f"arn:aws:bedrock:{region}::foundation-model/{EMBEDDING_MODEL_ID}",
                    "embeddingModelConfiguration": {
                        "bedrockEmbeddingModelConfiguration": {"dimensions": EMBEDDING_DIMENSION}
                    },
                },
            },
            storageConfiguration={
                "type": "OPENSEARCH_SERVERLESS",
                "opensearchServerlessConfiguration": {
                    "collectionArn": collection_arn,
                    "vectorIndexName": INDEX_NAME,
                    "fieldMapping": {
                        "vectorField": VECTOR_FIELD,
                        "textField": TEXT_FIELD,
                        "metadataField": METADATA_FIELD,
                    },
                },
            },
        )
        kb_id = response["knowledgeBase"]["knowledgeBaseId"]

    print("[7/9] Waiting for Knowledge Base to become ACTIVE...")
    while True:
        status = bedrock_agent.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"]["status"]
        if status == "ACTIVE":
            return kb_id
        if status == "FAILED":
            raise RuntimeError(f"Knowledge Base {KB_NAME} failed to create.")
        time.sleep(10)


def create_data_source(clients, kb_id, bucket_name):
    """Step 8: attach the S3 bucket as the Knowledge Base's data source,
    using Bedrock's default FIXED_SIZE chunking (fine for a demo). Returns
    the data_source_id."""
    bedrock_agent = clients["bedrock_agent"]

    existing = [
        ds for ds in bedrock_agent.list_data_sources(knowledgeBaseId=kb_id)["dataSourceSummaries"]
        if ds["name"] == DATA_SOURCE_NAME
    ]
    if existing:
        print(f"[8/9] Data source {DATA_SOURCE_NAME} already exists.")
        return existing[0]["dataSourceId"]

    print(f"[8/9] Creating data source {DATA_SOURCE_NAME}...")
    response = bedrock_agent.create_data_source(
        knowledgeBaseId=kb_id,
        name=DATA_SOURCE_NAME,
        dataSourceConfiguration={
            "type": "S3",
            "s3Configuration": {"bucketArn": f"arn:aws:s3:::{bucket_name}"},
        },
        vectorIngestionConfiguration={
            "chunkingConfiguration": {
                "chunkingStrategy": "FIXED_SIZE",
                "fixedSizeChunkingConfiguration": {"maxTokens": 300, "overlapPercentage": 20},
            }
        },
    )
    return response["dataSource"]["dataSourceId"]


def start_ingestion_job(clients, kb_id, data_source_id):
    """Step 9: start an ingestion job over whatever's currently in the S3
    bucket and poll until it reaches COMPLETE. Safe to call with an empty
    bucket -- it just completes immediately with zero documents indexed."""
    bedrock_agent = clients["bedrock_agent"]

    print("[9/9] Starting ingestion job...")
    response = bedrock_agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=data_source_id)
    job_id = response["ingestionJob"]["ingestionJobId"]

    while True:
        status = bedrock_agent.get_ingestion_job(
            knowledgeBaseId=kb_id, dataSourceId=data_source_id, ingestionJobId=job_id
        )["ingestionJob"]["status"]
        if status == "COMPLETE":
            print("[9/9] Ingestion job complete.")
            return
        if status == "FAILED":
            raise RuntimeError("Ingestion job failed.")
        time.sleep(5)


def provision(region):
    """Run all nine provisioning steps in order and print the .env values
    the Flask app needs. Requires interactive or --yes confirmation of the
    cost warning before the billable collection is created (step 4)."""
    clients = _clients(region)
    account_id = _account_id(clients)
    bucket_name = BUCKET_NAME_TEMPLATE.format(prefix=NAME_PREFIX, account_id=account_id)

    create_bucket(clients, bucket_name, region)
    create_security_policies(clients, COLLECTION_NAME)
    create_access_policy(clients, COLLECTION_NAME, account_id, region, ROLE_NAME)
    collection_id, collection_arn = create_collection(clients, COLLECTION_NAME)
    role_arn = create_iam_role(clients, ROLE_NAME, account_id, region, bucket_name, collection_arn)
    create_vector_index(clients, collection_id, region)
    kb_id = create_knowledge_base(clients, role_arn, collection_arn, region)
    data_source_id = create_data_source(clients, kb_id, bucket_name)
    start_ingestion_job(clients, kb_id, data_source_id)

    print("\nProvisioning complete. Add these to your .env:\n")
    print(f"KNOWLEDGE_BASE_ID={kb_id}")
    print(f"DATA_SOURCE_ID={data_source_id}")
    print(f"KB_DATA_BUCKET={bucket_name}")


def teardown(region):
    """Reverse every step of provision(), in reverse order, so the demo
    stack (and its cost) can be fully removed. Each delete call swallows
    "already gone" errors so teardown is safe to re-run."""
    clients = _clients(region)
    account_id = _account_id(clients)
    bucket_name = BUCKET_NAME_TEMPLATE.format(prefix=NAME_PREFIX, account_id=account_id)
    bedrock_agent = clients["bedrock_agent"]
    aoss = clients["aoss"]
    iam = clients["iam"]
    s3 = clients["s3"]

    kb_matches = [kb for kb in bedrock_agent.list_knowledge_bases()["knowledgeBaseSummaries"] if kb["name"] == KB_NAME]
    if kb_matches:
        kb_id = kb_matches[0]["knowledgeBaseId"]
        for ds in bedrock_agent.list_data_sources(knowledgeBaseId=kb_id)["dataSourceSummaries"]:
            print(f"Deleting data source {ds['dataSourceId']}...")
            bedrock_agent.delete_data_source(knowledgeBaseId=kb_id, dataSourceId=ds["dataSourceId"])
        print(f"Deleting Knowledge Base {kb_id}...")
        bedrock_agent.delete_knowledge_base(knowledgeBaseId=kb_id)

    try:
        iam.delete_role_policy(RoleName=ROLE_NAME, PolicyName=f"{ROLE_NAME}-permissions")
    except ClientError:
        pass
    try:
        iam.delete_role(RoleName=ROLE_NAME)
        print(f"Deleted role {ROLE_NAME}.")
    except ClientError:
        pass

    collections = aoss.list_collections(collectionFilters={"name": COLLECTION_NAME})["collectionSummaries"]
    if collections:
        print(f"Deleting collection {COLLECTION_NAME}...")
        aoss.delete_collection(id=collections[0]["id"])

    for policy_name, policy_type in [
        (f"{COLLECTION_NAME}-access", "data"),
        (f"{COLLECTION_NAME}-net", "network"),
        (f"{COLLECTION_NAME}-enc", "encryption"),
    ]:
        try:
            if policy_type == "data":
                aoss.delete_access_policy(name=policy_name, type=policy_type)
            else:
                aoss.delete_security_policy(name=policy_name, type=policy_type)
            print(f"Deleted policy {policy_name}.")
        except ClientError:
            pass

    try:
        objects = s3.list_objects_v2(Bucket=bucket_name).get("Contents", [])
        if objects:
            s3.delete_objects(Bucket=bucket_name, Delete={"Objects": [{"Key": o["Key"]} for o in objects]})
        s3.delete_bucket(Bucket=bucket_name)
        print(f"Deleted bucket {bucket_name}.")
    except ClientError:
        pass

    print("Teardown complete.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="us-east-1", help="AWS region to provision in.")
    parser.add_argument("--teardown", action="store_true", help="Delete all resources instead of creating them.")
    parser.add_argument("--yes", action="store_true", help="Skip the interactive confirmation prompt.")
    args = parser.parse_args()

    if args.teardown:
        if not args.yes:
            confirm = input(f"This will delete all resources prefixed '{NAME_PREFIX}'. Type 'yes' to continue: ")
            if confirm.strip().lower() != "yes":
                print("Aborted.")
                sys.exit(1)
        teardown(args.region)
        return

    print(COST_WARNING)
    if not args.yes:
        confirm = input("Type 'yes' to continue provisioning: ")
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            sys.exit(1)

    provision(args.region)


if __name__ == "__main__":
    main()
