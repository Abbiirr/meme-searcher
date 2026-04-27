from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Natural language search query")
    limit: int = Field(default=10, ge=1, le=100, description="Max results to return")
    include_debug: bool = Field(default=False)
    client_session_id: str | None = Field(default=None, max_length=128)
    owui_user_id: str | None = Field(default=None, max_length=256)


class SearchHit(BaseModel):
    rank: int
    base_rank: int | None = None
    image_id: str
    source_uri: str
    thumbnail_uri: str
    ocr_excerpt: str = ""
    retrieval_score: float
    rerank_score: float | None = None
    learned_score: float | None = None
    impression_id: str | None = None
    feedback_select_url: str | None = None
    feedback_reject_url: str | None = None
    feedback_undo_url: str | None = None


class SearchResponse(BaseModel):
    query: str
    intent: str
    total_returned: int
    hits: list[SearchHit]
    search_id: str | None = None
    ranker_version_id: str | None = None
    feedback_enabled: bool = False
    feedback_none_correct_url: str | None = None


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


class FeedbackJudgmentRequest(BaseModel):
    token: str = Field(..., min_length=16)
    csrf_token: str | None = None


class FeedbackJudgmentResponse(BaseModel):
    status: str
    judgment_id: str | None = None
    search_id: str | None = None
    impression_id: str | None = None
    pairs_created: int = 0
    tombstoned_judgments: int = 0
