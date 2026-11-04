"""Local vector knowledge with serialized, journaled document replacement."""
from contextlib import contextmanager
import json
import logging
import os
from typing import Optional
import uuid

from filelock import FileLock
from services.storage import chroma_directory, prepare_chroma_storage

logger = logging.getLogger(__name__)
CHROMA_DB_PATH = str(chroma_directory())
EMBEDDING_MODEL_NAME = 'all-MiniLM-L6-v2'
COLLECTION_NAME = 'security_knowledge'
CHUNK_SIZE = 400
CHUNK_OVERLAP = 50


class SecurityKnowledgeBase:
    def __init__(self, storage_path=None, collection=None, encoder=None):
        self.storage_path = prepare_chroma_storage(target=storage_path or CHROMA_DB_PATH)
        self._lock = FileLock(str(self.storage_path / '.aca-knowledge.lock'), timeout=60, mode=0o600)
        self._journal = self.storage_path / '.aca-replacement.json'
        self._encoder = encoder
        with self._lock:
            if collection is None:
                import chromadb
                from chromadb.config import Settings
                self.client = chromadb.PersistentClient(path=str(self.storage_path.resolve()),
                                                       settings=Settings(anonymized_telemetry=False))
                collection = self.client.get_or_create_collection(name=COLLECTION_NAME, metadata={'hnsw:space': 'cosine'})
            self.collection = collection
            self._recover()

    @property
    def encoder(self):
        if self._encoder is None:
            from sentence_transformers import SentenceTransformer
            self._encoder = SentenceTransformer(EMBEDDING_MODEL_NAME)
        return self._encoder

    def chunk_text(self, text: str) -> list[str]:
        words = text.split()
        return [' '.join(words[start:start + CHUNK_SIZE])
                for start in range(0, len(words), CHUNK_SIZE - CHUNK_OVERLAP)]

    def _sync_directory(self):
        descriptor = os.open(self.storage_path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write_journal(self, value):
        temporary = self.storage_path / ('.aca-replacement-' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('x', encoding='utf-8') as output:
                os.chmod(temporary, 0o600)
                json.dump(value, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self._journal)
            self._sync_directory()
        finally:
            temporary.unlink(missing_ok=True)

    def _clear_journal(self):
        self._journal.unlink()
        self._sync_directory()

    def _recover(self):
        if not self._journal.exists():
            return
        # The previous document snapshot is durable before the first vector write.
        # Every application reader holds the same process/filesystem lock and
        # completes this rollback before it can observe any partial replacement.
        pending = json.loads(self._journal.read_text())
        snapshot = pending['snapshot']
        self.collection.delete(where={'doc_id': pending['doc_id']})
        if snapshot['ids']:
            self.collection.upsert(**snapshot)
        self._clear_journal()

    @contextmanager
    def _access(self):
        with self._lock:
            self._recover()
            yield

    @staticmethod
    def _doc_id(doc_id):
        if not isinstance(doc_id, str) or not doc_id.strip() or len(doc_id) > 256:
            raise ValueError('Document ID must be a nonempty string of at most 256 characters')
        return doc_id

    def _snapshot(self, doc_id):
        previous = self.collection.get(where={'doc_id': doc_id}, include=['documents', 'metadatas', 'embeddings'])
        embeddings = previous.get('embeddings')
        if hasattr(embeddings, 'tolist'):
            embeddings = embeddings.tolist()
        fields = (previous.get('documents'), previous.get('metadatas'), embeddings)
        if previous['ids'] and any(value is None or len(value) != len(previous['ids']) for value in fields):
            raise ValueError('Previous document snapshot is incomplete; replacement is blocked')
        return {'ids': previous['ids'], 'documents': previous.get('documents') or [],
                'metadatas': previous.get('metadatas') or [], 'embeddings': embeddings if embeddings is not None else []}

    def add_document(self, doc_id: str, text: str, metadata: Optional[dict] = None) -> dict:
        doc_id = self._doc_id(doc_id)
        if not isinstance(text, str) or not text.strip():
            raise ValueError('Document text is empty; use delete_document to remove a document')
        metadata = dict(metadata or {})
        resource_type = metadata.get('resource_type', 'general')
        if not isinstance(resource_type, str) or not resource_type.strip():
            raise ValueError('Resource type must be a nonempty string')
        metadata['resource_type'] = resource_type.strip().lower()
        chunks = self.chunk_text(text)
        # Compute/validate embeddings before touching the previous document.
        embeddings = self.encoder.encode(chunks, show_progress_bar=False).tolist()
        new = {'ids': [f'{doc_id}__chunk_{i}' for i in range(len(chunks))],
               'documents': chunks, 'embeddings': embeddings,
               'metadatas': [{'resource_type': 'general', **(metadata or {}), 'doc_id': doc_id, 'chunk_index': i, 'total_chunks': len(chunks)}
                             for i in range(len(chunks))]}
        with self._access():
            previous = self._snapshot(doc_id)
            self._write_journal({'doc_id': doc_id, 'snapshot': previous})
            try:
                self.collection.upsert(**new)
                stale = sorted(set(previous['ids']) - set(new['ids']))
                if stale:
                    self.collection.delete(ids=stale)
                self._clear_journal()
            except BaseException:
                # If rollback itself fails, retain the journal and fail closed on
                # subsequent reads until recovery succeeds. A hard crash uses the
                # same persisted rollback at the next application access.
                self._recover()
                raise
        return {'chunks_added': len(chunks), 'doc_id': doc_id}

    def search(self, query: str, n_results: int = 3, resource_filter: Optional[str] = None) -> list[dict]:
        if not isinstance(n_results, int) or isinstance(n_results, bool) or not 1 <= n_results <= 20:
            raise ValueError('n_results must be between 1 and 20')
        if not isinstance(query, str) or not query.strip():
            raise ValueError('Search query is empty')
        if resource_filter is not None and (not isinstance(resource_filter, str) or not resource_filter.strip()):
            raise ValueError('Resource filter must be a nonempty string')
        with self._access():
            count = self.collection.count()
            if not count:
                return []
            # Read legacy UI labels (S3/IAM/Terraform) as well as canonical lowercase metadata.
            resource = resource_filter.strip().lower() if resource_filter else None
            where = {'resource_type': {'$in': sorted({resource, resource.upper(), resource.capitalize()})}} if resource else None
            if where:
                count = len(self.collection.get(where=where, include=[])['ids'])
                if not count:
                    return []
            embedding = self.encoder.encode([query], show_progress_bar=False).tolist()
            options = {'where': where} if where else {}
            results = self.collection.query(query_embeddings=embedding, n_results=min(n_results, count),
                                            include=['documents', 'metadatas', 'distances'], **options)
            return [{'text': text, 'metadata': {**meta, 'resource_type': meta.get('resource_type', 'general').lower()}, 'distance': float(distance),
                     'relevance_score': 1 - float(distance)}
                    for text, meta, distance in zip(results['documents'][0], results['metadatas'][0], results['distances'][0])]

    def delete_document(self, doc_id: str) -> dict:
        doc_id = self._doc_id(doc_id)
        with self._access():
            snapshot = self._snapshot(doc_id)
            if snapshot['ids']:
                self._write_journal({'doc_id': doc_id, 'snapshot': snapshot})
                try:
                    self.collection.delete(where={'doc_id': doc_id})
                    self._clear_journal()
                except BaseException:
                    self._recover()
                    raise
        return {'deleted_chunks': len(snapshot['ids']), 'doc_id': doc_id}

    def list_documents(self) -> list[dict]:
        with self._access():
            existing = self.collection.get(include=['metadatas'])
            seen = {}
            for meta in existing['metadatas']:
                doc_id = meta.get('doc_id', 'unknown')
                if doc_id not in seen:
                    seen[doc_id] = {'doc_id': doc_id, 'source': meta.get('source', 'unknown'),
                                    'resource_type': meta.get('resource_type', 'general').lower(), 'chunk_count': 0}
                seen[doc_id]['chunk_count'] += 1
            return sorted(seen.values(), key=lambda entry: entry['doc_id'])

    def get_document_count(self) -> int:
        with self._access():
            return self.collection.count()


# Importing the class for isolated tests/tools must not open the real database.
_knowledge_base = None


def __getattr__(name):
    if name != 'knowledge_base':
        raise AttributeError(name)
    global _knowledge_base
    if _knowledge_base is None:
        _knowledge_base = SecurityKnowledgeBase()
    return _knowledge_base
