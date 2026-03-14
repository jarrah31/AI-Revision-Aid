from pydantic import BaseModel, Field


# Auth
class SignupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6)
    display_name: str = Field(min_length=1, max_length=100)
    year_group: int = Field(ge=7, le=13)


class LoginRequest(BaseModel):
    username: str
    password: str


class ProfileUpdate(BaseModel):
    display_name: str | None = None
    year_group: int | None = Field(default=None, ge=7, le=13)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6)


class TokenResponse(BaseModel):
    token: str
    user: dict


# Subjects
class SubjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    icon: str | None = None
    color: str | None = None


class SubjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    icon: str | None = None
    color: str | None = None


# Questions
class QuestionUpdate(BaseModel):
    question_text: str | None = None
    answer_text: str | None = None
    question_type: str | None = None
    difficulty: int | None = Field(default=None, ge=1, le=3)


# Categories
class CategoryCreate(BaseModel):
    subject_id: int
    name: str = Field(min_length=1, max_length=100)


class CategoryUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class PageCategoryAssign(BaseModel):
    batch_id: int
    page_number: int
    category_id: int | None = None  # None removes the category
