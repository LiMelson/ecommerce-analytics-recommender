from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @field_validator("*", mode="before")
    @classmethod
    def strip_text(cls, value, info):
        if isinstance(value, str) and info.field_name not in {"password", "current_password"}:
            return value.strip()
        return value


class Login(StrictModel):
    username: str = Field(min_length=1, max_length=30)
    password: str = Field(min_length=1, max_length=128)


class AccountCreate(StrictModel):
    username: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,29}$")
    name: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=10, max_length=128)
    role_id: Literal["admin", "analyst", "viewer"] = "analyst"
    enabled: bool = True


class AccountUpdate(StrictModel):
    name: str = Field(min_length=1, max_length=40)
    role_id: Literal["admin", "analyst", "viewer"]
    enabled: bool


class PasswordReset(StrictModel):
    password: str = Field(min_length=10, max_length=128)


class PasswordChange(PasswordReset):
    current_password: str = Field(min_length=1, max_length=128)


class ProfileUpdate(StrictModel):
    name: str = Field(min_length=1, max_length=40)
    email: str = Field(default="", max_length=254)
    team: str = Field(default="", max_length=80)
    note: str = Field(default="", max_length=500)

    @field_validator("email")
    @classmethod
    def email_format(cls, value):
        if value and ("@" not in value or " " in value):
            raise ValueError("邮箱格式不正确")
        return value


class RoleUpdate(StrictModel):
    permissions: list[str] = Field(max_length=6)


class MessageCreate(StrictModel):
    type: Literal["功能建议", "问题反馈", "项目待办", "数据说明"]
    content: str = Field(min_length=1, max_length=500)


class MessageUpdate(StrictModel):
    done: bool


class FileImport(StrictModel):
    relative_path: str = Field(min_length=1, max_length=300)
    name: str = Field(default="", max_length=100)
    activate: bool = True


class ModelCreate(StrictModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    dataset_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    name: str = Field(min_length=1, max_length=100)
    metrics: dict[str, float] = Field(default_factory=dict, max_length=30)
    note: str = Field(default="", max_length=500)


class RecommendationItem(StrictModel):
    item_id: str = Field(min_length=1, max_length=128)
    category_id: str = Field(default="", max_length=128)
    rank: int = Field(ge=1, le=10)
    score: float | None = Field(default=None, allow_inf_nan=False)
    reason: str = Field(default="模型排序结果", max_length=200)


class RecommendationResult(StrictModel):
    user_id: str
    dataset_id: str
    model_version: str
    items: list[RecommendationItem] = Field(max_length=10)
