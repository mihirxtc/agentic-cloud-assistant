import logging
import os
from typing import Optional

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

CHROMA_DB_PATH = os.path.join(os.path.dirname(__file__), "../../chroma_db")
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
COLLECTION_NAME = "security_knowledge"
CHUNK_SIZE = 400
CHUNK_OVERLAP = 50


class SecurityKnowledgeBase:
    def __init__(self):
        self.client = chromadb.PersistentClient(
            path=os.path.abspath(CHROMA_DB_PATH),
            settings=Settings(anonymized_telemetry=False),
        )

        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
        )
        self._encoder = None

    @property
    def encoder(self):
        if self._encoder is None:
            logger.info("Loading embedding model — first load takes 10-15 seconds...")
            self._encoder = SentenceTransformer(EMBEDDING_MODEL_NAME)
            logger.info("Embedding model ready.")
        return self._encoder

    def chunk_text(self, text: str) -> list[str]:
        words = text.split()
        chunks = []
        start = 0
        while start < len(words):
            end = start + CHUNK_SIZE
            chunk = " ".join(words[start:end])
            if chunk.strip():
                chunks.append(chunk)
            start += CHUNK_SIZE - CHUNK_OVERLAP
        return chunks

    def add_document(
        self, doc_id: str, text: str, metadata: Optional[dict] = None
    ) -> dict:
        metadata = metadata or {}
        chunks = self.chunk_text(text)
        if not chunks:
            return {"chunks_added": 0, "doc_id": doc_id}
        embeddings = self.encoder.encode(chunks, show_progress_bar=False).tolist()
        chunk_ids = [f"{doc_id}__chunk_{i}" for i in range(len(chunks))]
        chunk_metadatas = [
            {
                **metadata,
                "doc_id": doc_id,
                "chunk_index": i,
                "total_chunks": len(chunks),
            }
            for i in range(len(chunks))
        ]
        self.collection.upsert(
            ids=chunk_ids,
            embeddings=embeddings,
            documents=chunks,
            metadatas=chunk_metadatas,
        )
        logger.info(f"Stored '{doc_id}' as {len(chunks)} chunks")
        return {"chunks_added": len(chunks), "doc_id": doc_id}

    def search(self, query: str, n_results: int = 3) -> list[dict]:
        if self.get_document_count() == 0:
            return []
        query_embedding = self.encoder.encode([query], show_progress_bar=False).tolist()
        results = self.collection.query(
            query_embeddings=query_embedding,
            n_results=min(n_results, self.get_document_count()),
            include=["documents", "metadatas", "distances"],
        )
        output = []
        for i in range(len(results["documents"][0])):
            output.append(
                {
                    "text": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i],
                    "distance": round(results["distances"][0][i], 4),
                    "relevance_score": round(1 - results["distances"][0][i], 4),
                }
            )
        return output

    def delete_document(self, doc_id: str) -> dict:
        existing = self.collection.get()
        ids_to_delete = [
            id_ for id_ in existing["ids"] if id_.startswith(f"{doc_id}__chunk_")
        ]
        if ids_to_delete:
            self.collection.delete(ids=ids_to_delete)
        return {"deleted_chunks": len(ids_to_delete), "doc_id": doc_id}

    def list_documents(self) -> list[dict]:
        existing = self.collection.get(include=["metadatas"])
        seen = {}
        for meta in existing["metadatas"]:
            doc_id = meta.get("doc_id", "unknown")
            if doc_id not in seen:
                seen[doc_id] = {
                    "doc_id": doc_id,
                    "source": meta.get("source", "unknown"),
                    "resource_type": meta.get("resource_type", "general"),
                    "chunk_count": 0,
                }
            seen[doc_id]["chunk_count"] += 1
        return list(seen.values())

    def get_document_count(self) -> int:
        return self.collection.count()


knowledge_base = SecurityKnowledgeBase()
