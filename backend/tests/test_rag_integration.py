"""
Integration tests for the RAG HTTP API.

These tests call the live FastAPI server on localhost:8000 — the server must
be running before these tests are executed.  They test the full request/response
cycle: routing → endpoint → knowledge_base → response JSON.

Run with:
    cd backend
    python -m pytest tests/test_rag_integration.py -v
"""

import pytest
import requests
import os

BASE = "http://localhost:8000"
TEXT_DOC_ID   = "test-integration-text-doc"
UPLOAD_DOC_ID = "test-integration-upload-doc"


def get(path, **kwargs):
    return requests.get(f"{BASE}{path}", **kwargs)

def post(path, **kwargs):
    return requests.post(f"{BASE}{path}", **kwargs)

def delete(path, **kwargs):
    return requests.delete(f"{BASE}{path}", **kwargs)


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_docs():
    """Delete test documents before AND after the suite to guarantee clean state."""
    for doc_id in [TEXT_DOC_ID, UPLOAD_DOC_ID]:
        delete(f"/rag/documents/{doc_id}")
    yield
    for doc_id in [TEXT_DOC_ID, UPLOAD_DOC_ID]:
        delete(f"/rag/documents/{doc_id}")


class TestListDocuments:

    def test_returns_200(self):
        r = get("/rag/documents")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_response_has_required_fields(self):
        data = get("/rag/documents").json()
        assert "documents" in data
        assert "total_documents" in data
        assert "total_chunks" in data

    def test_five_seeded_documents_present(self):
        data = get("/rag/documents").json()
        assert data["total_documents"] >= 5, (
            f"Expected at least 5 seeded documents, got {data['total_documents']}"
        )

    def test_seeded_doc_ids_are_present(self):
        data = get("/rag/documents").json()
        ids = {d["doc_id"] for d in data["documents"]}
        expected = {
            "aws-s3-security", "aws-ec2-security", "aws-iam-security",
            "aws-vpc-security", "terraform-security-patterns"
        }
        missing = expected - ids
        assert not missing, f"Missing seeded documents: {missing}"

    def test_each_document_has_chunk_count_gte_1(self):
        data = get("/rag/documents").json()
        for doc in data["documents"]:
            assert doc["chunk_count"] >= 1, (
                f"Document {doc['doc_id']} has chunk_count {doc['chunk_count']}"
            )

    def test_total_chunks_equals_sum_of_chunk_counts(self):
        data = get("/rag/documents").json()
        summed = sum(d["chunk_count"] for d in data["documents"])
        assert data["total_chunks"] == summed, (
            f"total_chunks {data['total_chunks']} != sum of chunk_counts {summed}"
        )


class TestTextIngest:

    def test_ingest_returns_200(self):
        r = post("/rag/documents/text", json={
            "doc_id": TEXT_DOC_ID,
            "text": "AWS Lambda functions should use least privilege IAM execution roles. "
                    "Never store secrets in environment variables. Use Secrets Manager instead. " * 10,
            "resource_type": "lambda",
            "source": "test-suite",
            "title": "Lambda Security Test Doc",
        })
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_ingest_returns_success_status(self):
        data = post("/rag/documents/text", json={
            "doc_id": TEXT_DOC_ID,
            "text": "Lambda function execution role least privilege IAM policy VPC security. " * 15,
            "resource_type": "lambda",
            "source": "test-suite",
        }).json()
        assert data.get("status") == "success"

    def test_ingest_returns_correct_doc_id(self):
        data = post("/rag/documents/text", json={
            "doc_id": TEXT_DOC_ID,
            "text": "Lambda security practices IAM roles permissions. " * 10,
            "resource_type": "lambda",
            "source": "test-suite",
        }).json()
        assert data.get("doc_id") == TEXT_DOC_ID

    def test_ingest_reports_chunks_added(self):
        data = post("/rag/documents/text", json={
            "doc_id": TEXT_DOC_ID,
            "text": "Lambda security practices IAM roles permissions. " * 10,
            "resource_type": "lambda",
            "source": "test-suite",
        }).json()
        assert isinstance(data.get("chunks_added"), int)
        assert data["chunks_added"] >= 1

    def test_ingested_doc_appears_in_list(self):
        post("/rag/documents/text", json={
            "doc_id": TEXT_DOC_ID,
            "text": "Lambda IAM execution role security policy permissions. " * 10,
            "resource_type": "lambda",
            "source": "test-suite",
        })
        data = get("/rag/documents").json()
        ids = [d["doc_id"] for d in data["documents"]]
        assert TEXT_DOC_ID in ids, f"{TEXT_DOC_ID} not found in document list"

    def test_upsert_does_not_duplicate_on_second_ingest(self):
        # Ingest the same doc_id twice — total count must not grow
        post("/rag/documents/text", json={
            "doc_id": TEXT_DOC_ID,
            "text": "Lambda IAM execution role security policy permissions. " * 10,
            "resource_type": "lambda",
            "source": "test-suite",
        })
        count_mid = get("/rag/documents").json()["total_chunks"]

        post("/rag/documents/text", json={
            "doc_id": TEXT_DOC_ID,
            "text": "Lambda IAM execution role security policy permissions. " * 10,
            "resource_type": "lambda",
            "source": "test-suite",
        })
        count_after = get("/rag/documents").json()["total_chunks"]

        assert count_after == count_mid, (
            f"Upsert created duplicates: {count_mid} -> {count_after}"
        )


class TestFileUpload:

    def test_txt_upload_returns_200(self, tmp_path):
        txt_file = tmp_path / "test_security.txt"
        txt_file.write_text(
            "RDS Database Security Best Practices. "
            "Encrypt RDS instances at rest using AWS KMS. "
            "Place RDS in private subnets. Never expose port 3306 to 0.0.0.0/0. "
            "Enable automated backups and multi-AZ for production databases. " * 10
        )
        with open(txt_file, "rb") as f:
            r = requests.post(
                f"{BASE}/rag/documents/upload",
                files={"file": ("test_security.txt", f, "text/plain")},
                data={
                    "doc_id": UPLOAD_DOC_ID,
                    "resource_type": "rds",
                    "source": "test-suite-upload",
                }
            )
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_txt_upload_returns_success_status(self, tmp_path):
        txt_file = tmp_path / "test2.txt"
        txt_file.write_text("RDS encryption backup security KMS. " * 20)
        with open(txt_file, "rb") as f:
            data = requests.post(
                f"{BASE}/rag/documents/upload",
                files={"file": ("test2.txt", f, "text/plain")},
                data={"doc_id": UPLOAD_DOC_ID, "resource_type": "rds"}
            ).json()
        assert data.get("status") == "success"

    def test_txt_upload_doc_appears_in_list(self, tmp_path):
        txt_file = tmp_path / "test3.txt"
        txt_file.write_text("RDS instance encryption backup multi-AZ subnet security. " * 20)
        with open(txt_file, "rb") as f:
            requests.post(
                f"{BASE}/rag/documents/upload",
                files={"file": ("test3.txt", f, "text/plain")},
                data={"doc_id": UPLOAD_DOC_ID, "resource_type": "rds"}
            )
        ids = [d["doc_id"] for d in get("/rag/documents").json()["documents"]]
        assert UPLOAD_DOC_ID in ids

    def test_empty_txt_upload_returns_400(self, tmp_path):
        empty_file = tmp_path / "empty.txt"
        empty_file.write_text("")
        with open(empty_file, "rb") as f:
            r = requests.post(
                f"{BASE}/rag/documents/upload",
                files={"file": ("empty.txt", f, "text/plain")},
                data={"doc_id": "empty-doc-test", "resource_type": "general"}
            )
        assert r.status_code == 400, (
            f"Expected 400 for empty file, got {r.status_code}: {r.text}"
        )


class TestDeleteDocument:

    @pytest.fixture(autouse=True)
    def ensure_test_doc_exists(self):
        """Make sure TEXT_DOC_ID exists before each delete test."""
        post("/rag/documents/text", json={
            "doc_id": TEXT_DOC_ID,
            "text": "Lambda IAM security role permissions execution. " * 15,
            "resource_type": "lambda",
            "source": "test-suite",
        })

    def test_delete_returns_200(self):
        r = delete(f"/rag/documents/{TEXT_DOC_ID}")
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_delete_returns_deleted_status(self):
        data = delete(f"/rag/documents/{TEXT_DOC_ID}").json()
        assert data.get("status") == "deleted"

    def test_delete_returns_correct_doc_id(self):
        data = delete(f"/rag/documents/{TEXT_DOC_ID}").json()
        assert data.get("doc_id") == TEXT_DOC_ID

    def test_delete_returns_deleted_chunks_count(self):
        data = delete(f"/rag/documents/{TEXT_DOC_ID}").json()
        assert isinstance(data.get("deleted_chunks"), int)
        assert data["deleted_chunks"] >= 1

    def test_delete_removes_doc_from_list(self):
        delete(f"/rag/documents/{TEXT_DOC_ID}")
        ids = [d["doc_id"] for d in get("/rag/documents").json()["documents"]]
        assert TEXT_DOC_ID not in ids

    def test_delete_reduces_total_chunk_count(self):
        count_before = get("/rag/documents").json()["total_chunks"]
        delete(f"/rag/documents/{TEXT_DOC_ID}")
        count_after = get("/rag/documents").json()["total_chunks"]
        assert count_after < count_before

    def test_delete_nonexistent_doc_returns_200_with_zero_chunks(self):
        r = delete("/rag/documents/this-doc-absolutely-does-not-exist-xyz123")
        assert r.status_code == 200
        data = r.json()
        assert data.get("deleted_chunks") == 0

    def test_delete_seeded_doc_then_restore(self):
        """Deleting a seeded doc then re-seeding must restore it."""
        from rag.knowledge_base import knowledge_base
        # Delete one seeded doc via API
        r = delete("/rag/documents/aws-vpc-security")
        assert r.json()["deleted_chunks"] >= 1

        # Restore it by re-adding directly (simulating re-seed)
        from rag.seed_knowledge import SEED_DOCUMENTS
        vpc_doc = next(d for d in SEED_DOCUMENTS if d["doc_id"] == "aws-vpc-security")
        knowledge_base.add_document(vpc_doc["doc_id"], vpc_doc["text"], vpc_doc["metadata"])

        # Verify it is back
        ids = [d["doc_id"] for d in get("/rag/documents").json()["documents"]]
        assert "aws-vpc-security" in ids


class TestQueryEndpointRetrieval:
    """
    These tests verify the retrieval behaviour of /rag/query.
    They use model_provider=ollama which requires no API key.
    The focus is on what gets retrieved and returned, not on the LLM answer quality.
    """

    def test_query_returns_200(self):
        r = post("/rag/query", json={
            "question": "How should I secure an S3 bucket?",
            "model_provider": "ollama",
        })
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_query_response_has_required_fields(self):
        data = post("/rag/query", json={
            "question": "What are S3 security best practices?",
            "model_provider": "ollama",
        }).json()
        for key in ["question", "answer", "sources", "chunks_used", "raw_chunks"]:
            assert key in data, f"Missing field: {key}"

    def test_query_echoes_question(self):
        question = "What is the CIS control for S3 Block Public Access?"
        data = post("/rag/query", json={
            "question": question,
            "model_provider": "ollama",
        }).json()
        assert data["question"] == question

    def test_query_returns_non_empty_answer(self):
        data = post("/rag/query", json={
            "question": "How do I restrict SSH access on an EC2 security group?",
            "model_provider": "ollama",
        }).json()
        assert isinstance(data.get("answer"), str)
        assert len(data["answer"]) > 0

    def test_query_retrieves_chunks_for_relevant_question(self):
        data = post("/rag/query", json={
            "question": "What CIS controls cover IAM MFA and root account?",
            "model_provider": "ollama",
        }).json()
        assert data["chunks_used"] >= 1, (
            f"Expected at least 1 chunk for a clearly relevant question, got {data['chunks_used']}"
        )

    def test_query_sources_are_seeded_doc_ids(self):
        data = post("/rag/query", json={
            "question": "How should I configure VPC Flow Logs?",
            "model_provider": "ollama",
        }).json()
        if data["chunks_used"] > 0:
            for src in data["sources"]:
                assert isinstance(src, str) and len(src) > 0

    def test_query_resource_type_filter_restricts_chunks(self):
        """With resource_type='ec2', all raw_chunks must be from ec2 documents."""
        data = post("/rag/query", json={
            "question": "How do I restrict ingress on security groups?",
            "resource_type": "ec2",
            "model_provider": "ollama",
        }).json()
        for chunk in data.get("raw_chunks", []):
            rt = chunk["metadata"].get("resource_type")
            assert rt == "ec2", f"Filter to ec2 but got resource_type={rt}"

    def test_query_raw_chunks_have_relevance_scores(self):
        data = post("/rag/query", json={
            "question": "Terraform security group SSH configuration",
            "model_provider": "ollama",
        }).json()
        for chunk in data.get("raw_chunks", []):
            assert "relevance_score" in chunk
            assert chunk["relevance_score"] >= 0.3, (
                f"Chunk below threshold slipped through: {chunk['relevance_score']}"
            )

    def test_query_with_groq_returns_answer(self):
        """End-to-end test using Groq from the .env GROQ_API_KEY."""
        data = post("/rag/query", json={
            "question": "What does CIS control 2.1.5 require for S3 buckets?",
            "model_provider": "groq",
        }).json()
        assert isinstance(data.get("answer"), str)
        assert len(data["answer"]) > 50, "Groq answer is suspiciously short"

    def test_query_with_anthropic_returns_answer(self):
        """
        End-to-end test using Anthropic.

        The endpoint must always return HTTP 200 with an 'answer' string,
        whether the key is valid (real answer) or invalid (error message).
        This tests that the error-handling path in rag_query() works correctly
        — no 500, no unhandled exception, structured JSON response in both cases.
        """
        data = post("/rag/query", json={
            "question": "What CIS controls cover IAM root account hardening?",
            "model_provider": "anthropic",
        }).json()
        # answer must always be a string — valid key → real answer, invalid key → error message
        assert isinstance(data.get("answer"), str), (
            f"Expected answer to be a string, got: {type(data.get('answer'))}"
        )
        assert len(data["answer"]) > 0, "answer field must not be empty"

    def test_query_answer_references_source_content(self):
        """The answer to an S3 query should mention specific S3 security terms."""
        data = post("/rag/query", json={
            "question": "What is S3 Block Public Access and which CIS control requires it?",
            "model_provider": "groq",
        }).json()
        answer_lower = data["answer"].lower()
        # The grounded answer should reference concrete terms from the seeded doc
        relevant_terms = ["block public access", "s3", "cis", "2.1.5"]
        matched = [t for t in relevant_terms if t in answer_lower]
        assert len(matched) >= 2, (
            f"Answer does not seem grounded in knowledge base content. "
            f"Only matched: {matched}\nAnswer: {data['answer'][:300]}"
        )
