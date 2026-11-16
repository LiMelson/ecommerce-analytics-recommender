import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from . import responses as o
from . import schemas as s
from .db import get_db
from .models import Account, LoginSession, Role, audit
from .security import (
    COOKIE,
    DEFAULT_ROLES,
    DUMMY_HASH,
    PASSWORDS,
    PERMISSIONS,
    account_json,
    principal,
    require,
    revoke_sessions,
    token_hash,
)

router = APIRouter(prefix="/api/v1", tags=["账号与权限"])


def session_json(db, account, csrf):
    role = db.get(Role, account.role_id)
    return {"account": account_json(account, role.name), "permissions": role.permissions, "csrf_token": csrf}


@router.post("/auth/login", response_model=o.Session)
def login(body: s.Login, request: Request, response: Response, db=Depends(get_db)):
    request.app.state.login_limiter.check(request.client.host if request.client else "local")
    account = db.get(Account, body.username)
    valid = PASSWORDS.verify(body.password, account.password_hash if account else DUMMY_HASH)
    if not valid or not account or not account.enabled:
        audit(db, body.username[:30], "系统", "登录失败", "账号、密码不正确或账号已停用", "失败")
        db.commit()
        raise HTTPException(401, "账号、密码不正确或账号已停用")
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    settings = request.app.state.settings
    ttl = settings.session_hours * 3600
    db.execute(delete(LoginSession).where(LoginSession.expires_at <= int(time.time())))
    db.add(
        LoginSession(
            token_hash=token_hash(token), account_id=account.username, csrf=csrf, expires_at=int(time.time()) + ttl
        )
    )
    audit(db, account.username, "系统", "登录")
    db.commit()
    response.set_cookie(
        COOKIE, token, max_age=ttl, httponly=True, secure=settings.cookie_secure, samesite="lax", path="/api"
    )
    return session_json(db, account, csrf)


@router.get("/auth/me", response_model=o.Session)
def me(user=Depends(principal), db=Depends(get_db)):
    return session_json(db, user["account"], user["csrf"])


@router.post("/auth/logout", response_model=o.Ok)
def logout(request: Request, response: Response, user=Depends(principal), db=Depends(get_db)):
    db.execute(delete(LoginSession).where(LoginSession.token_hash == token_hash(request.cookies.get(COOKIE, ""))))
    audit(db, user["account"].username, "系统", "退出登录")
    db.commit()
    response.delete_cookie(COOKIE, path="/api")
    return {"ok": True}


@router.patch("/auth/me", response_model=o.Account)
def update_me(body: s.ProfileUpdate, user=Depends(principal), db=Depends(get_db)):
    account = db.get(Account, user["account"].username)
    for key, value in body.model_dump().items():
        setattr(account, key, value)
    audit(db, account.username, "系统管理", "修改个人资料")
    db.commit()
    return account_json(account, user["role"])


@router.post("/auth/password", response_model=o.Ok)
def change_password(body: s.PasswordChange, response: Response, user=Depends(principal), db=Depends(get_db)):
    account = db.get(Account, user["account"].username)
    if not PASSWORDS.verify(body.current_password, account.password_hash):
        raise HTTPException(400, "当前密码不正确")
    account.password_hash = PASSWORDS.hash(body.password)
    revoke_sessions(db, account.username)
    audit(db, account.username, "系统管理", "修改登录密码", "该账号全部会话已失效")
    db.commit()
    response.delete_cookie(COOKIE, path="/api")
    return {"ok": True}


@router.get("/accounts", response_model=o.Page[o.Account])
def accounts(page: int = 1, page_size: int = 50, user=Depends(require("账号管理")), db=Depends(get_db)):
    from .common import pagination

    return pagination(
        db,
        select(Account).order_by(Account.created_at, Account.username),
        page,
        page_size,
        lambda a: account_json(a, db.get(Role, a.role_id).name),
    )


@router.post("/accounts", status_code=201, response_model=o.Account)
def create_account(body: s.AccountCreate, user=Depends(require("账号管理")), db=Depends(get_db)):
    account = Account(
        username=body.username,
        name=body.name,
        password_hash=PASSWORDS.hash(body.password),
        role_id=body.role_id,
        enabled=body.enabled,
    )
    db.add(account)
    audit(db, user["account"].username, "系统管理", "创建账号", body.username)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "账号已存在") from None
    return account_json(account, db.get(Role, account.role_id).name)


@router.patch("/accounts/{username}", response_model=o.Account)
def update_account(username: str, body: s.AccountUpdate, user=Depends(require("账号管理")), db=Depends(get_db)):
    # Serialize administrator role changes to protect the last enabled administrator.
    db.execute(select(Role).where(Role.id == "admin").with_for_update()).scalar_one()
    account = db.get(Account, username)
    if not account:
        raise HTTPException(404, "账号不存在")
    if username == user["account"].username and (not body.enabled or body.role_id != account.role_id):
        raise HTTPException(409, "不能停用自己或修改自己的角色")
    if account.role_id == "admin" and account.enabled and (not body.enabled or body.role_id != "admin"):
        admins = db.scalars(select(Account).where(Account.role_id == "admin", Account.enabled.is_(True))).all()
        if len(admins) <= 1:
            raise HTTPException(409, "至少保留一个启用的管理员")
    changed_auth = account.role_id != body.role_id or account.enabled != body.enabled
    for key, value in body.model_dump().items():
        setattr(account, key, value)
    if changed_auth:
        revoke_sessions(db, username)
    audit(db, user["account"].username, "系统管理", "修改账号", username)
    db.commit()
    return account_json(account, db.get(Role, account.role_id).name)


@router.post("/accounts/{username}/password", response_model=o.Ok)
def reset_password(username: str, body: s.PasswordReset, user=Depends(require("账号管理")), db=Depends(get_db)):
    account = db.get(Account, username)
    if not account:
        raise HTTPException(404, "账号不存在")
    account.password_hash = PASSWORDS.hash(body.password)
    revoke_sessions(db, username)
    audit(db, user["account"].username, "系统管理", "重置账号密码", username)
    db.commit()
    return {"ok": True}


@router.get("/roles", response_model=o.Roles)
def roles(user=Depends(principal), db=Depends(get_db)):
    return {
        "permissions": PERMISSIONS,
        "items": [
            {"id": r.id, "name": r.name, "permissions": r.permissions}
            for r in db.scalars(select(Role).order_by(Role.id))
        ],
    }


@router.patch("/roles/{role_id}", response_model=o.Role)
def update_role(role_id: str, body: s.RoleUpdate, user=Depends(require("账号管理")), db=Depends(get_db)):
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(404, "角色不存在")
    if set(body.permissions) - set(PERMISSIONS):
        raise HTTPException(422, "包含未知权限")
    if "数据概览" not in body.permissions:
        raise HTTPException(409, "所有角色至少保留数据概览权限")
    # Management rights belong exclusively to administrators.
    if role_id == "admin" and set(body.permissions) != set(PERMISSIONS):
        raise HTTPException(409, "管理员保留全部系统权限")
    if role_id != "admin" and "账号管理" in body.permissions:
        raise HTTPException(409, "账号管理权限只授予管理员")
    role.permissions = [p for p in PERMISSIONS if p in body.permissions]
    audit(db, user["account"].username, "系统管理", "修改角色权限", role.name)
    db.commit()
    return {"id": role.id, "name": role.name, "permissions": role.permissions}


def bootstrap(db, password):
    for role_id, (name, permissions) in DEFAULT_ROLES.items():
        if not db.get(Role, role_id):
            db.add(Role(id=role_id, name=name, permissions=permissions))
    db.flush()
    if db.get(Account, "admin"):
        return False
    db.add(Account(username="admin", name="管理员", role_id="admin", password_hash=PASSWORDS.hash(password)))
    audit(db, "admin", "系统", "初始化管理员")
    db.commit()
    return True
