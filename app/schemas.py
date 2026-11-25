from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator


class FeedCreate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    url: AnyHttpUrl


class FeedRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    url: str
    is_active: bool
    last_fetched_at: datetime | None
    last_success_at: datetime | None
    last_error: str | None


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    created_at: datetime


class SubscriptionRead(BaseModel):
    user_id: int
    feed_id: int
    created: bool
    matched_existing_articles: int


class ArticleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    feed_id: int
    title: str
    url: str | None
    summary: str | None
    published_at: datetime | None
    fetched_at: datetime


class RefreshResult(BaseModel):
    feed_id: int
    feed_name: str
    success: bool
    added: int = 0
    skipped: int = 0
    error: str | None = None


class FilterKeywordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    keyword: str


class FilterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    filter_type: str = "keyword"
    keywords: list[str] = Field(default_factory=list)
    match_mode: str = "ANY"
    semantic_query: str | None = None
    semantic_threshold: float | None = None
    is_active: bool = True
    user_id: int | None = None

    @model_validator(mode="after")
    def valid_configuration(self) -> "FilterCreate":
        if self.filter_type == "keyword":
            if not any(item.strip() for item in self.keywords):
                raise ValueError("At least one keyword is required")
            if self.semantic_query is not None or self.semantic_threshold is not None:
                raise ValueError("Keyword filters cannot have semantic settings")
        elif self.filter_type == "semantic":
            if self.keywords:
                raise ValueError("Semantic filters do not use keywords")
            if not self.semantic_query or not self.semantic_query.strip():
                raise ValueError("Semantic query is required")
            if self.semantic_threshold is None:
                self.semantic_threshold = 0.83
            if not 0.0 <= self.semantic_threshold <= 1.0:
                raise ValueError("Semantic threshold must be between 0 and 1")
        else:
            raise ValueError("filter_type must be keyword or semantic")
        return self


class FilterUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    filter_type: str | None = None
    keywords: list[str] | None = None
    match_mode: str | None = None
    semantic_query: str | None = None
    semantic_threshold: float | None = None
    is_active: bool | None = None


class FilterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    name: str
    filter_type: str
    match_mode: str
    semantic_query: str | None
    semantic_threshold: float | None
    is_active: bool
    keywords: list[FilterKeywordRead]
    created_at: datetime
    updated_at: datetime


class FilterMutationResult(BaseModel):
    filter: FilterRead
    matched_existing_articles: int


class ArticleMatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    filter_id: int
    method: str
    score: float
    matched_keywords: list[str]
    matched_fields: dict[str, list[str]]
    details: dict
    created_at: datetime


class FilteredArticleRead(ArticleRead):
    feed_name: str
    matches: list[ArticleMatchRead]


class TestRSSPostCreate(BaseModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    content: str | None = None
    author: str | None = Field(default=None, max_length=255)


class TestRSSPostRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    guid: str
    title: str
    description: str
    content: str | None
    author: str | None
    published_at: datetime
    created_at: datetime


class TestRSSPublishResult(BaseModel):
    post: TestRSSPostRead
    refresh: RefreshResult
