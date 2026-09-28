from pydantic import BaseModel, Field, field_validator


def _normalize_email(value: str) -> str:
    email = value.strip().lower()
    if "@" not in email or not email.split("@", 1)[0]:
        raise ValueError("Invalid email address")
    return email


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _normalize_email(value)

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class LoginResponse(BaseModel):
    ok: bool = True


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


class RoleBrief(BaseModel):
    id: int
    code: str
    name: str

    model_config = {"from_attributes": True}


class MeResponse(BaseModel):
    id: int
    email: str
    name: str
    is_active: bool
    roles: list[RoleBrief]
    permissions: list[str]


class UserCreate(BaseModel):
    email: str
    name: str
    password: str = Field(min_length=8)
    role_ids: list[int] = []
    is_active: bool = True

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _normalize_email(value)


class UserUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    role_ids: list[int] | None = None
    password: str | None = Field(default=None, min_length=8)


class UserOut(BaseModel):
    id: int
    email: str
    name: str
    is_active: bool
    roles: list[RoleBrief]

    model_config = {"from_attributes": True}


class RoleCreate(BaseModel):
    code: str = Field(min_length=2, max_length=64)
    name: str
    description: str | None = None


class RoleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class RoleOut(BaseModel):
    id: int
    code: str
    name: str
    description: str | None
    is_system: bool
    permission_codes: list[str] = []

    model_config = {"from_attributes": True}


class PermissionOut(BaseModel):
    id: int
    code: str
    name: str
    module: str
    description: str | None

    model_config = {"from_attributes": True}


class RolePermissionsUpdate(BaseModel):
    permission_codes: list[str]


class ProjectMemberCreate(BaseModel):
    user_id: int
    member_role: str


class ProjectMemberOut(BaseModel):
    id: int
    user_id: int
    user_email: str
    user_name: str
    member_role: str
    assigned_at: str

    model_config = {"from_attributes": True}
