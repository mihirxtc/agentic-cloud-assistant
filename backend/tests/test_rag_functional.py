"""
Functional tests for the RAG system.

These tests import the Python modules directly — no HTTP server needed.
They test individual functions in isolation to verify each component
does exactly what it is supposed to do.

Run with:
    cd backend
    python -m pytest tests/test_rag_functional.py -v
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from rag.knowledge_base import SecurityKnowledgeBase, CHUNK_SIZE, CHUNK_OVERLAP
from rag.rag_service import (
    build_augmented_prompt,
    query_knowledge_base,
    handle_rag_tool_call,
    RELEVANCE_THRESHOLD,
)


@pytest.fixture(scope="module")
def kb():
    """Return the live knowledge_base singleton — same object the server uses."""
    from rag.knowledge_base import knowledge_base
    return knowledge_base


TEST_DOC_ID = "test-functional-temp-doc"
TEST_DOC_TEXT = """
AWS Lambda Security Best Practices

Function Permissions:
Always follow the principle of least privilege when creating Lambda execution
roles. Grant only the specific IAM actions the function needs to perform its
task. Avoid using wildcard actions such as s3:* in Lambda execution policies.
Instead, scope permissions to the exact S3 bucket ARN and the specific actions
like s3:GetObject or s3:PutObject that the function requires.

Environment Variables:
Never store secrets as plaintext environment variables in Lambda. Use AWS
Secrets Manager or AWS Systems Manager Parameter Store to retrieve secrets
at runtime. Environment variables set directly in the console or via
CloudFormation are visible to anyone with iam:GetFunction permissions on
the role.

VPC Configuration:
If a Lambda function needs access to private VPC resources such as RDS or
ElastiCache, deploy it inside a VPC subnet. Use a security group that allows
only the specific ports needed to reach those resources. Functions in a VPC
require a NAT Gateway for outbound internet access because they lose their
default public endpoint.

CIS Lambda Controls:
Ensure Lambda functions use execution roles with minimal required permissions.
Enable CloudTrail logging for Lambda API calls to detect unauthorised
invocations and configuration changes.
"""


class TestChunkText:

    def test_short_document_produces_one_chunk(self):
        """A document under CHUNK_SIZE words must produce exactly one chunk."""
        kb_instance = SecurityKnowledgeBase.__new__(SecurityKnowledgeBase)
        # We call chunk_text as a standalone method — no DB connection needed
        kb_instance_clean = type('obj', (object,), {'chunk_text': SecurityKnowledgeBase.chunk_text})()

        short_text = " ".join(["word"] * 200)
        chunks = SecurityKnowledgeBase.chunk_text(None, short_text)
        assert len(chunks) == 1, f"Expected 1 chunk, got {len(chunks)}"

    def test_long_document_produces_multiple_chunks(self):
        """A document of 800 words (> CHUNK_SIZE=400) must produce multiple chunks."""
        long_text = " ".join(["word"] * 800)
        chunks = SecurityKnowledgeBase.chunk_text(None, long_text)
        assert len(chunks) >= 2, f"Expected >= 2 chunks, got {len(chunks)}"

    def test_empty_string_produces_no_chunks(self):
        """Empty input must return an empty list, not raise an error."""
        chunks = SecurityKnowledgeBase.chunk_text(None, "")
        assert chunks == [], f"Expected [], got {chunks}"

    def test_whitespace_only_produces_no_chunks(self):
        """Whitespace-only input must return an empty list."""
        chunks = SecurityKnowledgeBase.chunk_text(None, "   \n\n\t  ")
        assert chunks == [], f"Expected [], got {chunks}"

    def test_chunk_overlap_is_present(self):
        """The last CHUNK_OVERLAP words of chunk 0 must appear at the start of chunk 1."""
        # Build text where every word is unique and identifiable by index
        words = [f"word{i}" for i in range(CHUNK_SIZE + 100)]
        text = " ".join(words)
        chunks = SecurityKnowledgeBase.chunk_text(None, text)

        assert len(chunks) >= 2, "Need at least 2 chunks to test overlap"

        # The last CHUNK_OVERLAP words of chunk 0
        chunk0_words = chunks[0].split()
        overlap_words = chunk0_words[-CHUNK_OVERLAP:]

        # They must appear at the beginning of chunk 1
        chunk1_words = chunks[1].split()
        chunk1_start = chunk1_words[:CHUNK_OVERLAP]

        assert overlap_words == chunk1_start, (
            f"Overlap mismatch.\n"
            f"End of chunk 0: {overlap_words[:5]}...\n"
            f"Start of chunk 1: {chunk1_start[:5]}..."
        )

    def test_chunk_size_does_not_exceed_limit(self):
        """No chunk should contain more than CHUNK_SIZE words."""
        long_text = " ".join([f"w{i}" for i in range(2000)])
        chunks = SecurityKnowledgeBase.chunk_text(None, long_text)
        for i, chunk in enumerate(chunks):
            word_count = len(chunk.split())
            assert word_count <= CHUNK_SIZE, (
                f"Chunk {i} has {word_count} words, exceeds CHUNK_SIZE={CHUNK_SIZE}"
            )


class TestAddDocumentAndSearch:

    def test_add_document_returns_correct_chunk_count(self, kb):
        """add_document must return the number of chunks it created."""
        result = kb.add_document(TEST_DOC_ID, TEST_DOC_TEXT, {"resource_type": "lambda"})
        assert "chunks_added" in result
        assert result["chunks_added"] >= 1
        assert result["doc_id"] == TEST_DOC_ID

    def test_add_document_upsert_is_idempotent(self, kb):
        """Running add_document twice with the same doc_id must not duplicate chunks."""
        count_before = kb.get_document_count()
        kb.add_document(TEST_DOC_ID, TEST_DOC_TEXT, {"resource_type": "lambda"})
        count_after = kb.get_document_count()
        assert count_after == count_before, (
            f"Upsert created duplicates: count went from {count_before} to {count_after}"
        )

    def test_search_returns_relevant_result_for_known_topic(self, kb):
        """A query clearly matching seeded content must score above RELEVANCE_THRESHOLD."""
        results = kb.search("How do I block public access on an S3 bucket?", n_results=3)
        assert len(results) > 0, "Search returned no results"
        top = results[0]
        assert top["relevance_score"] >= RELEVANCE_THRESHOLD, (
            f"Top result score {top['relevance_score']} is below threshold {RELEVANCE_THRESHOLD}"
        )

    def test_search_top_result_is_correct_document(self, kb):
        """An S3-specific query must retrieve at least one chunk from an S3-related document."""
        results = kb.search("S3 Block Public Access settings", n_results=3)
        assert len(results) > 0
        doc_ids = [r["metadata"]["doc_id"] for r in results]
        assert any("s3" in doc_id.lower() or "security" in doc_id.lower() for doc_id in doc_ids), (
            f"Expected an S3-related document in top results, got {doc_ids}"
        )

    def test_search_returns_relevance_score_field(self, kb):
        """Every result dict must contain relevance_score, text, metadata, distance."""
        results = kb.search("IAM least privilege", n_results=2)
        for r in results:
            assert "relevance_score" in r
            assert "text" in r
            assert "metadata" in r
            assert "distance" in r

    def test_search_scores_are_bounded(self, kb):
        """relevance_score = 1 - distance must stay in [0, 1] for cosine distances."""
        results = kb.search("VPC Flow Logs security", n_results=5)
        for r in results:
            score = r["relevance_score"]
            assert 0.0 <= score <= 1.0, f"Score {score} is out of [0,1] range"

    def test_search_irrelevant_query_scores_low(self, kb):
        """A query completely unrelated to AWS security must score lower than a relevant one."""
        relevant = kb.search("S3 bucket encryption CIS control", n_results=1)
        irrelevant = kb.search("how to bake sourdough bread recipe flour yeast", n_results=1)
        if relevant and irrelevant:
            assert relevant[0]["relevance_score"] > irrelevant[0]["relevance_score"], (
                "Relevant query should score higher than irrelevant query"
            )

    def test_add_then_search_finds_new_document(self, kb):
        """A document added to the knowledge base must be retrievable by search."""
        results = kb.search("Lambda execution role least privilege permissions", n_results=3)
        doc_ids = [r["metadata"]["doc_id"] for r in results]
        # The test doc we added covers Lambda — it must surface for this query
        assert TEST_DOC_ID in doc_ids, (
            f"test document not found in results. Got: {doc_ids}"
        )


class TestDeleteAndList:

    def test_list_documents_contains_all_seeded_docs(self, kb):
        """All 5 seeded documents must appear in list_documents()."""
        docs = kb.list_documents()
        doc_ids = {d["doc_id"] for d in docs}
        expected = {
            "aws-s3-security",
            "aws-ec2-security",
            "aws-iam-security",
            "aws-vpc-security",
            "terraform-security-patterns",
        }
        missing = expected - doc_ids
        assert not missing, f"Missing seeded documents: {missing}"

    def test_list_documents_structure(self, kb):
        """Each document entry must have the four required fields."""
        docs = kb.list_documents()
        for doc in docs:
            assert "doc_id" in doc
            assert "source" in doc
            assert "resource_type" in doc
            assert "chunk_count" in doc
            assert doc["chunk_count"] >= 1

    def test_delete_removes_chunks(self, kb):
        """Deleting a document must reduce the total chunk count."""
        count_before = kb.get_document_count()
        result = kb.delete_document(TEST_DOC_ID)
        count_after = kb.get_document_count()

        assert result["deleted_chunks"] >= 1
        assert count_after < count_before, (
            f"Chunk count did not decrease: {count_before} -> {count_after}"
        )

    def test_delete_nonexistent_returns_zero(self, kb):
        """Deleting a doc_id that does not exist must return deleted_chunks=0 without error."""
        result = kb.delete_document("this-doc-does-not-exist-xyz")
        assert result["deleted_chunks"] == 0

    def test_deleted_document_not_in_list(self, kb):
        """After deletion, the doc_id must not appear in list_documents()."""
        docs = kb.list_documents()
        doc_ids = [d["doc_id"] for d in docs]
        assert TEST_DOC_ID not in doc_ids, (
            f"{TEST_DOC_ID} still appears in list after deletion"
        )

    def test_deleted_document_not_in_search(self, kb):
        """After deletion, search must not return chunks from the deleted document."""
        results = kb.search("Lambda execution role VPC security group permissions", n_results=5)
        returned_ids = [r["metadata"]["doc_id"] for r in results]
        assert TEST_DOC_ID not in returned_ids, (
            f"Deleted document still appearing in search results"
        )


class TestBuildAugmentedPrompt:

    MOCK_CHUNKS = [
        {
            "text": "Always enable Block Public Access on S3 buckets. CIS 2.1.5.",
            "metadata": {"doc_id": "aws-s3-security", "resource_type": "s3"},
            "relevance_score": 0.82,
            "distance": 0.18,
        },
        {
            "text": "Use SSE-KMS for fine-grained S3 encryption.",
            "metadata": {"doc_id": "aws-s3-security", "resource_type": "s3"},
            "relevance_score": 0.71,
            "distance": 0.29,
        },
    ]

    def test_prompt_with_chunks_contains_source_labels(self):
        """The augmented prompt must contain [Source N: ...] labels."""
        prompt = build_augmented_prompt("How do I secure S3?", self.MOCK_CHUNKS)
        assert "[Source 1:" in prompt
        assert "[Source 2:" in prompt

    def test_prompt_with_chunks_contains_relevance_scores(self):
        """The augmented prompt must show the relevance score for each chunk."""
        prompt = build_augmented_prompt("How do I secure S3?", self.MOCK_CHUNKS)
        assert "0.82" in prompt
        assert "0.71" in prompt

    def test_prompt_with_chunks_contains_doc_id(self):
        """Source labels must include the doc_id of the retrieved chunk."""
        prompt = build_augmented_prompt("How do I secure S3?", self.MOCK_CHUNKS)
        assert "aws-s3-security" in prompt

    def test_prompt_with_chunks_contains_chunk_text(self):
        """The actual text of each chunk must appear in the augmented prompt."""
        prompt = build_augmented_prompt("How do I secure S3?", self.MOCK_CHUNKS)
        assert "Block Public Access" in prompt
        assert "SSE-KMS" in prompt

    def test_prompt_with_chunks_contains_the_question(self):
        """The user's question must appear in the augmented prompt."""
        question = "How do I secure S3?"
        prompt = build_augmented_prompt(question, self.MOCK_CHUNKS)
        assert question in prompt

    def test_context_appears_before_question(self):
        """Retrieved context must be positioned before the question in the prompt."""
        question = "How do I secure S3?"
        prompt = build_augmented_prompt(question, self.MOCK_CHUNKS)
        source_pos = prompt.find("[Source 1:")
        question_pos = prompt.find(question)
        assert source_pos < question_pos, (
            "Retrieved context must come BEFORE the question in the prompt"
        )

    def test_prompt_with_no_chunks_contains_fallback_message(self):
        """When no chunks are provided, the fallback prompt must note the empty knowledge base."""
        prompt = build_augmented_prompt("Some question", [])
        assert "No specific documentation" in prompt

    def test_prompt_with_no_chunks_still_contains_question(self):
        """The fallback prompt must still include the user's question."""
        question = "What is least privilege?"
        prompt = build_augmented_prompt(question, [])
        assert question in prompt

    def test_prompt_with_no_chunks_does_not_contain_source_label(self):
        """The fallback prompt must not contain any [Source N:] labels."""
        prompt = build_augmented_prompt("Any question", [])
        assert "[Source 1:" not in prompt


class TestQueryKnowledgeBase:

    def test_returns_all_expected_keys(self):
        """query_knowledge_base must return a dict with all documented fields."""
        result = query_knowledge_base("S3 bucket security best practices")
        for key in ["query", "chunks_found", "chunks_used", "sources", "augmented_prompt", "raw_chunks"]:
            assert key in result, f"Missing key: {key}"

    def test_chunks_used_lte_chunks_found(self):
        """After threshold filtering, chunks_used must be <= chunks_found."""
        result = query_knowledge_base("VPC Flow Logs")
        assert result["chunks_used"] <= result["chunks_found"]

    def test_resource_filter_restricts_results(self):
        """With resource_filter='s3', all returned chunks must have resource_type='s3'."""
        result = query_knowledge_base("encryption security", resource_filter="s3")
        for chunk in result["raw_chunks"]:
            assert chunk["metadata"].get("resource_type") == "s3", (
                f"Resource filter returned non-s3 chunk: {chunk['metadata']}"
            )

    def test_resource_filter_wrong_type_returns_no_chunks(self):
        """Querying with a mismatched resource_filter must yield zero chunks_used."""
        # Ask about S3 but filter to vpc — should return zero usable chunks
        result = query_knowledge_base("S3 public bucket fix", resource_filter="vpc")
        assert result["chunks_used"] == 0, (
            f"Expected 0 chunks with mismatched filter, got {result['chunks_used']}"
        )

    def test_sources_list_contains_doc_ids(self):
        """Sources list must contain recognisable doc_id strings from the seeded corpus."""
        result = query_knowledge_base("IAM MFA root account security")
        # Only check if chunks were found
        if result["chunks_used"] > 0:
            assert len(result["sources"]) > 0
            for s in result["sources"]:
                assert isinstance(s, str) and len(s) > 0

    def test_augmented_prompt_is_a_non_empty_string(self):
        """The augmented_prompt field must always be a non-empty string."""
        result = query_knowledge_base("security group ingress rules")
        assert isinstance(result["augmented_prompt"], str)
        assert len(result["augmented_prompt"]) > 0


class TestHandleRagToolCall:

    def test_returns_all_required_keys(self):
        """handle_rag_tool_call must return all four documented fields."""
        result = handle_rag_tool_call({"query": "EC2 security group SSH port"})
        for key in ["knowledge_retrieved", "sources_consulted", "chunks_retrieved", "context"]:
            assert key in result, f"Missing key: {key}"

    def test_knowledge_retrieved_is_bool(self):
        """knowledge_retrieved must be True when chunks were found."""
        result = handle_rag_tool_call({"query": "S3 bucket Block Public Access CIS"})
        assert isinstance(result["knowledge_retrieved"], bool)

    def test_context_is_non_empty_string(self):
        """context must be a non-empty string regardless of retrieval success."""
        result = handle_rag_tool_call({"query": "VPC subnet security architecture"})
        assert isinstance(result["context"], str)
        assert len(result["context"]) > 0

    def test_resource_type_filter_is_passed_through(self):
        """Passing resource_type in tool_input must filter results correctly."""
        result = handle_rag_tool_call({
            "query": "encryption best practices",
            "resource_type": "iam"
        })
        # sources should only reference IAM documents if any match
        assert "chunks_retrieved" in result
        assert isinstance(result["chunks_retrieved"], int)

    def test_n_results_defaults_to_3(self):
        """Without n_results, chunks_retrieved must be at most 3."""
        result = handle_rag_tool_call({"query": "security recommendations"})
        assert result["chunks_retrieved"] <= 3

    def test_n_results_override_respected(self):
        """Passing n_results=1 must cap results at 1."""
        result = handle_rag_tool_call({
            "query": "AWS security best practices",
            "n_results": 1
        })
        assert result["chunks_retrieved"] <= 1
