import logging
from typing import Optional
from rag.knowledge_base import knowledge_base

logger = logging.getLogger(__name__)

RELEVANCE_THRESHOLD = 0.3


def build_augmented_prompt(query: str, chunks: list[dict]) -> str:
    if not chunks:
        return f"""You are an AWS cloud security expert assistant.
Answer the following question using your training knowledge.
Note: No specific documentation was found in the knowledge base for this query.

Question: {query}"""

    context_sections = []
    for i, chunk in enumerate(chunks):
        source = chunk["metadata"].get("doc_id", "unknown")
        relevance = chunk["relevance_score"]
        context_sections.append(
            f"[Source {i+1}: {source} | relevance: {relevance}]\n{chunk['text']}"
        )
    context_text = "\n\n---\n\n".join(context_sections)

    return f"""You are an AWS cloud security expert assistant for the \
Agentic Cloud Assistant system.

The following knowledge has been retrieved from the security knowledge base:

{context_text}

---

Using the retrieved knowledge above as your primary reference, answer this question:
{query}

If the retrieved knowledge directly answers the question, base your response on it.
If the knowledge is only partially relevant, supplement with your training knowledge \
and clearly indicate which parts come from the retrieved documents."""


def query_knowledge_base(
    query: str,
    n_results: int = 3,
    resource_filter: Optional[str] = None
) -> dict:
    raw_chunks = knowledge_base.search(query, n_results=n_results)
    relevant_chunks = [
        c for c in raw_chunks
        if c["relevance_score"] >= RELEVANCE_THRESHOLD
    ]
    if resource_filter:
        relevant_chunks = [
            c for c in relevant_chunks
            if c["metadata"].get("resource_type") == resource_filter
        ]
    sources = list(set(
        c["metadata"].get("doc_id", "unknown")
        for c in relevant_chunks
    ))
    augmented_prompt = build_augmented_prompt(query, relevant_chunks)
    return {
        "query": query,
        "chunks_found": len(raw_chunks),
        "chunks_used": len(relevant_chunks),
        "sources": sources,
        "augmented_prompt": augmented_prompt,
        "raw_chunks": relevant_chunks
    }

RAG_TOOL_DEFINITION = {
    "name": "query_security_knowledge_base",
    "description": (
        "Search the security knowledge base for AWS best practices, "
        "CIS benchmark controls, Terraform security patterns, and past "
        "audit findings. Use this when the user asks about security "
        "recommendations, compliance requirements, or how to fix a "
        "specific AWS misconfiguration. Returns grounded knowledge "
        "with source attribution."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The security question or topic to search for"
            },
            "resource_type": {
                "type": "string",
                "enum": [
                    "ec2", "s3", "iam", "vpc",
                    "rds", "terraform", "general"
                ],
                "description": (
                    "Optional: filter results to a specific AWS resource type"
                )
            },
            "n_results": {
                "type": "integer",
                "description": (
                    "Number of knowledge chunks to retrieve (default 3, max 5)"
                ),
                "default": 3
            }
        },
        "required": ["query"]
    }
}


def handle_rag_tool_call(tool_input: dict) -> dict:
    result = query_knowledge_base(
        query=tool_input["query"],
        n_results=tool_input.get("n_results", 3),
        resource_filter=tool_input.get("resource_type")
    )
    return {
        "knowledge_retrieved": result["chunks_used"] > 0,
        "sources_consulted": result["sources"],
        "chunks_retrieved": result["chunks_used"],
        "context": result["augmented_prompt"]
    }
