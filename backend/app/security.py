import hashlib
import secrets
import time
from collections import OrderedDict
from threading import Lock

from fastapi import Depends, HTTPException, Request
from pwdlib import PasswordHash
from sqlalchemy import delete

from .models import Account, LoginSession, Role

PASSWORDS = PasswordHash.recommended()
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(24))
PERMISSIONS = ["数据概览", "行为分析", "推荐查看", "数据导入", "任务管理", "账号管理"]
DEFAULT_ROLES = {
    "admin": ("管理员", PERMISSIONS),
    "analyst": ("分析员", PERMISSIONS[:4]),
    "viewer": ("访客", PERMISSIONS[:3]),
}
COOKIE = "commerce_session"


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class LoginLimiter:
    """Bounded per-process limiter; deploy one API process or add a shared limiter."""

    def __init__(self):
        self.entries = OrderedDict()
        self.lock = Lock()

    def check(self, key):
        with self.lock:
            now = time.time()
            attempts = [t for t in self.entries.pop(key, []) if t > now - 300]
            if len(attempts) >= 10:
                self.entries[key] = attempts
                raise HTTPException(429, "登录尝试过多，请五分钟后重试")
            self.entries[key] = attempts + [now]
            while len(self.entries) > 4096:
                self.entries.popitem(last=False)


def principal(request: Request):
    token = request.cookies.get(COOKIE, "")
    if not token:
        raise HTTPException(401, "请先登录")
    with request.app.state.db.session() as db:
        session = db.get(LoginSession, token_hash(token))
        if not session or session.expires_at <= time.time():
            raise HTTPException(401, "登录已过期，请重新登录")
        account = db.get(Account, session.account_id)
        if not account or not account.enabled:
            raise HTTPException(401, "账号已停用或登录已失效")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            csrf = request.headers.get("X-CSRF-Token", "")
            if not secrets.compare_digest(csrf, session.csrf):
                raise HTTPException(403, "请求校验失败，请刷新页面后重试")
        role = db.get(Role, account.role_id)
        return {"account": account, "permissions": role.permissions, "role": role.name, "csrf": session.csrf}


def require(permission):
    def guard(user=Depends(principal)):
        if permission not in user["permissions"]:
            raise HTTPException(403, "当前账号没有此操作权限")
        return user

    return guard


def revoke_sessions(db, username):
    db.execute(delete(LoginSession).where(LoginSession.account_id == username))


def account_json(account, role_name):
    return {
        "id": account.username,
        "name": account.name,
        "role_id": account.role_id,
        "role": role_name,
        "enabled": account.enabled,
        "email": account.email,
        "team": account.team,
        "note": account.note,
        "created_at": account.created_at,
    }
