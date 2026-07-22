"""Node functions for the Corrective RAG graph.

Each node is a plain function of (state) -> partial state update, which is
the contract LangGraph expects. Inside each node we build a small LCEL chain
(prompt | model | parser) -- this is the "LCEL and LangGraph are separate,
composable layers" teaching point from the plan: LangGraph owns the control
flow (the retry loop), LCEL owns each individual model call.
"""

from langchain_aws import AmazonKnowledgeBasesRetriever, ChatBedrockConverse
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.bedrock_clients import get_bedrock_agent_runtime_client, get_bedrock_runtime_client
from app.config import Config

# Maximum number of rewrite -> retrieve loops before generate() is called
# anyway with a low-confidence framing. Guarantees the graph always
# terminates, even for questions the Knowledge Base can't answer.
MAX_REWRITES = 2


def _chat_model():
    """Build the ChatBedrockConverse instance shared by grading, rewriting,
    and generation. A fresh instance per call keeps nodes independent and
    easy to read in training material, at the cost of a small amount of
    duplicate client setup."""
    return ChatBedrockConverse(
        model=Config.BEDROCK_MODEL_ID,
        client=get_bedrock_runtime_client(),
        temperature=0,
    )


def retrieve(state):
    """Fetch chunks from the Bedrock Knowledge Base for the current
    `question`. Uses AmazonKnowledgeBasesRetriever from langchain_aws (not
    the deprecated langchain_community version) with an explicit
    bedrock-agent-runtime client so it shares the same IAM credential chain
    as every other AWS call in the app."""
    retriever = AmazonKnowledgeBasesRetriever(
        knowledge_base_id=Config.KNOWLEDGE_BASE_ID,
        region_name=Config.AWS_REGION,
        client=get_bedrock_agent_runtime_client(),
        retrieval_config={"vectorSearchConfiguration": {"numberOfResults": 4}},
    )
    documents = retriever.invoke(state["question"])
    return {"documents": documents}


def grade_documents(state):
    """Ask the chat model whether the retrieved documents actually answer
    the original question. This is the "corrective" step in Corrective RAG:
    instead of blindly generating from whatever was retrieved, we gate on
    relevance first so a bad retrieval can trigger a query rewrite instead
    of a confidently wrong answer."""
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You grade whether retrieved documents are relevant to a "
                "question. Respond with exactly one word: 'relevant' or "
                "'irrelevant'.",
            ),
            (
                "human",
                "Question: {question}\n\nRetrieved documents:\n{documents}",
            ),
        ]
    )
    chain = prompt | _chat_model() | StrOutputParser()

    documents_text = "\n\n".join(doc.page_content for doc in state["documents"])
    verdict = chain.invoke(
        {"question": state["original_question"], "documents": documents_text}
    )

    grade = "relevant" if "relevant" in verdict.lower() and "irrelevant" not in verdict.lower() else "irrelevant"
    return {"grade": grade}


def rewrite_query(state):
    """Ask the chat model to rewrite the retrieval query when
    grade_documents() found the last retrieval irrelevant. Increments
    rewrite_count so the conditional edge in build.py can enforce
    MAX_REWRITES and guarantee the graph terminates."""
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "Rewrite the user's question into a better search query for "
                "a document retrieval system. Return only the rewritten "
                "query, nothing else.",
            ),
            ("human", "{question}"),
        ]
    )
    chain = prompt | _chat_model() | StrOutputParser()

    rewritten = chain.invoke({"question": state["question"]})
    return {
        "question": rewritten.strip(),
        "rewrite_count": state["rewrite_count"] + 1,
    }


def generate(state):
    """Produce the final answer from the original question and whatever
    documents are currently in state, and extract citation metadata for the
    UI. Called either after a relevant grade, or after MAX_REWRITES is
    reached -- in the latter case the prompt is told to hedge, so the app
    never presents a low-confidence answer as if retrieval fully backed it.
    """
    low_confidence = state["grade"] == "irrelevant" and state["rewrite_count"] >= MAX_REWRITES

    system_message = (
        "Answer the question using only the provided documents. Cite facts "
        "from the documents where possible."
    )
    if low_confidence:
        system_message += (
            " The retrieved documents may not fully answer the question -- "
            "say so explicitly and answer as best you can from what's "
            "available."
        )

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_message),
            ("human", "Question: {question}\n\nDocuments:\n{documents}"),
        ]
    )
    chain = prompt | _chat_model() | StrOutputParser()

    documents_text = "\n\n".join(doc.page_content for doc in state["documents"])
    answer = chain.invoke(
        {"question": state["original_question"], "documents": documents_text}
    )

    citations = [doc.metadata for doc in state["documents"]]
    return {"answer": answer, "citations": citations}


def route_after_grading(state):
    """Conditional-edge function consumed by add_conditional_edges() in
    build.py. Returns the name of the next node: "generate" once documents
    are relevant (or the retry budget is exhausted), otherwise
    "rewrite_query" to try again."""
    if state["grade"] == "relevant":
        return "generate"
    if state["rewrite_count"] >= MAX_REWRITES:
        return "generate"
    return "rewrite_query"
