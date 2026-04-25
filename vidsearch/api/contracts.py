from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Natural language search query")
    limit: int = Field(default=10, ge=1, le=20, description="Max results to return")
    include_debug: bool = Field(default=False)


class SearchHit(BaseModel):
    rank: int
    image_id: str
    source_uri: str
    thumbnail_uri: str
    ocr_excerpt: str = ""
    retrieval_score: float
    rerank_score: float | None = None


class SearchResponse(BaseModel):
    query: str
    intent: str
    total_returned: int
    hits: list[SearchHit]


class IngestImageRequest(BaseModel):
    path: str


class IngestFolderRequest(BaseModel):
    folder: str


class DeleteImageResponse(BaseModel):
    image_id: str
    deleted: bool
    message: str = ""


class FeedbackRequest(BaseModel):
    query_text: str
    image_id: str | None = None
    signal: str
    value: float | None = None


class FeedbackResponse(BaseModel):
    event_id: str
    status: str = "recorded"
