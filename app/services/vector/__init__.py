"""向量服务子包

整合向量嵌入、检索、索引、存储、文档分割等模块。
"""

from app.services.vector.embedding import vector_embedding_service, DashScopeEmbeddings
from app.services.vector.search import vector_search_service, VectorSearchService, SearchResult
from app.services.vector.index import vector_index_service, VectorIndexService, IndexingResult
from app.services.vector.store import vector_store_manager, VectorStoreManager
from app.services.vector.splitter import document_splitter_service, DocumentSplitterService

__all__ = [
    "vector_embedding_service",
    "DashScopeEmbeddings",
    "vector_search_service",
    "VectorSearchService",
    "SearchResult",
    "vector_index_service",
    "VectorIndexService",
    "IndexingResult",
    "vector_store_manager",
    "VectorStoreManager",
    "document_splitter_service",
    "DocumentSplitterService",
]
