"""Public response contracts used by OpenAPI and runtime response validation."""

from datetime import date
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


T = TypeVar("T")


class Page(Output, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class DatasetPage(Page[T], Generic[T]):
    dataset_id: str


class Account(Output):
    id: str
    name: str
    role_id: str
    role: str
    enabled: bool
    email: str
    team: str
    note: str
    created_at: int


class Session(Output):
    account: Account
    permissions: list[str]
    csrf_token: str


class Role(Output):
    id: str
    name: str
    permissions: list[str]


class Roles(Output):
    permissions: list[str]
    items: list[Role]


class Dataset(Output):
    id: str
    name: str
    filename: str
    source: str
    is_demo: bool
    status: Literal["queued", "importing", "ready", "failed"]
    row_count: int
    start_ms: int | None
    end_ms: int | None
    sha256: str
    summary: dict[str, Any]
    active: bool
    created_at: int


class ActiveDataset(Output):
    dataset: Dataset | None


class Job(Output):
    id: str
    kind: Literal["import", "export"]
    status: Literal["queued", "running", "ready", "failed"]
    dataset_id: str
    processed_rows: int
    error: str
    created_at: int
    updated_at: int


class ImportResult(Output):
    dataset: Dataset
    job: Job


class Counts(Output):
    pv: int
    fav: int
    cart: int
    buy: int


class Daily(Counts):
    day: date
    users: int


class Overview(Output):
    dataset_id: str
    counts: Counts
    total: int
    users: int
    items: int
    categories: int
    days: list[Daily]


class Metric(Output):
    dataset_id: str
    items: list[dict[str, Any]]


class Behavior(Output):
    id: int
    user: str
    item: str
    category: str
    type: Literal["pv", "fav", "cart", "buy"]
    t: int


class UserSummary(Output):
    user: str
    total: int
    itemCount: int
    counts: Counts
    last: int
    recency: int | None
    frequency: int
    segment: str


class UserProfile(UserSummary):
    dataset_id: str
    cats: list[tuple[str, int]]
    rows: list[Behavior]


class Log(Output):
    id: int
    actor: str
    module: str
    action: str
    detail: str
    status: str
    created_at: int


class Message(Output):
    id: int
    author_id: str
    name: str
    type: str
    content: str
    done: bool
    created_at: int


class Model(Output):
    id: str
    dataset_id: str
    name: str
    metrics: dict[str, float]
    note: str
    created_at: int


class ActiveModel(Output):
    status: Literal["not_connected", "registered", "incompatible"]
    model: Model | None


class Ok(Output):
    ok: bool


class ErrorDetail(Output):
    field: str
    message: str


class ErrorBody(Output):
    code: int
    message: str
    details: list[ErrorDetail] | None = None


class ErrorReply(Output):
    error: ErrorBody
    request_id: str
