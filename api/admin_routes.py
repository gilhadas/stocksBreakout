"""
Admin dashboard and API.
Protected by X-Admin-Secret header (ADMIN_SECRET in .env).
Dashboard: GET /admin
API: GET/POST/DELETE /admin/users, PATCH /admin/users/{id}/password
"""
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api.database import get_db
from api.models import User
from api.auth import hash_password

_root = Path(__file__).resolve().parent.parent
load_dotenv(_root / '.env')

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin(x_admin_secret: str = Header(default="")):
    secret = os.getenv("ADMIN_SECRET", "")
    if not secret or x_admin_secret != secret:
        raise HTTPException(status_code=403, detail="Forbidden")


# ── API ───────────────────────────────────────────────────────────────────────

@router.get("/users")
def list_users(db: Session = Depends(get_db), _=Depends(_require_admin)):
    users = db.query(User).order_by(User.created_at).all()
    return [
        {
            "id":         u.id,
            "email":      u.email,
            "name":       u.name or "",
            "auth":       ("password+google" if u.password_hash and u.google_id
                           else "google" if u.google_id
                           else "password" if u.password_hash
                           else "none"),
            "created_at": u.created_at.strftime("%Y-%m-%d %H:%M") if u.created_at else "",
            "last_login": u.last_login.strftime("%Y-%m-%d %H:%M") if u.last_login else "never",
        }
        for u in users
    ]


class CreateUserRequest(BaseModel):
    email: str
    name: str = ""
    password: str = ""


@router.post("/users", status_code=201)
def create_user(req: CreateUserRequest, db: Session = Depends(get_db), _=Depends(_require_admin)):
    if db.query(User).filter(User.email == req.email.lower()).first():
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(
        id=str(uuid.uuid4()),
        email=req.email.lower(),
        password_hash=hash_password(req.password) if req.password else None,
        name=req.name or req.email.split("@")[0],
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    return {"ok": True, "id": user.id, "email": user.email}


@router.delete("/users/{user_id}")
def delete_user(user_id: str, db: Session = Depends(get_db), _=Depends(_require_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    default_id = os.getenv("DEFAULT_USER_ID", "")
    if user_id == default_id:
        raise HTTPException(status_code=400, detail="Cannot delete the default admin user")
    db.delete(user)
    db.commit()
    return {"ok": True}


class ResetPasswordRequest(BaseModel):
    password: str


@router.patch("/users/{user_id}/password")
def reset_password(user_id: str, req: ResetPasswordRequest, db: Session = Depends(get_db), _=Depends(_require_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not req.password:
        raise HTTPException(status_code=400, detail="Password cannot be empty")
    user.password_hash = hash_password(req.password)
    db.commit()
    return {"ok": True}


# ── Dashboard HTML ────────────────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>StocksBreakout — Admin</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #0f1117; color: #e2e8f0; min-height: 100vh; }
  header { background: #1a1d27; border-bottom: 1px solid #2d3748;
           padding: 16px 32px; display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 18px; font-weight: 600; color: #f7fafc; }
  header span { font-size: 12px; background: #2d3748; color: #a0aec0;
                padding: 2px 8px; border-radius: 99px; }
  main { max-width: 960px; margin: 32px auto; padding: 0 24px; }

  .card { background: #1a1d27; border: 1px solid #2d3748; border-radius: 10px;
          padding: 24px; margin-bottom: 24px; }
  .card h2 { font-size: 14px; font-weight: 600; color: #a0aec0;
             text-transform: uppercase; letter-spacing: .05em; margin-bottom: 16px; }

  /* login */
  #login-card { max-width: 360px; margin: 80px auto; }
  .field { margin-bottom: 14px; }
  .field label { display: block; font-size: 13px; color: #a0aec0; margin-bottom: 4px; }
  input[type=text], input[type=password], input[type=email] {
    width: 100%; padding: 8px 12px; background: #0f1117; border: 1px solid #4a5568;
    border-radius: 6px; color: #e2e8f0; font-size: 14px; outline: none; }
  input:focus { border-color: #667eea; }

  /* buttons */
  .btn { padding: 8px 16px; border-radius: 6px; font-size: 13px; font-weight: 500;
         cursor: pointer; border: none; transition: opacity .15s; }
  .btn:hover { opacity: .85; }
  .btn-primary { background: #667eea; color: #fff; }
  .btn-danger  { background: #e53e3e; color: #fff; }
  .btn-sm { padding: 4px 10px; font-size: 12px; }

  /* users table */
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; padding: 8px 12px; color: #718096; font-weight: 500;
       border-bottom: 1px solid #2d3748; }
  td { padding: 10px 12px; border-bottom: 1px solid #1e2533; vertical-align: middle; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #1e2533; }

  .badge { font-size: 11px; padding: 2px 7px; border-radius: 99px; font-weight: 500; }
  .badge-pw { background: #2c5282; color: #90cdf4; }
  .badge-g  { background: #276749; color: #9ae6b4; }
  .badge-pg { background: #553c9a; color: #d6bcfa; }
  .badge-none { background: #4a5568; color: #a0aec0; }

  .email { font-weight: 500; }
  .mono  { font-family: monospace; font-size: 11px; color: #718096; }

  /* add-user form */
  .form-row { display: flex; gap: 10px; flex-wrap: wrap; align-items: flex-end; }
  .form-row .field { flex: 1; min-width: 140px; margin-bottom: 0; }

  /* toast */
  #toast { position: fixed; bottom: 24px; right: 24px; padding: 12px 20px;
           border-radius: 8px; font-size: 13px; font-weight: 500; opacity: 0;
           transition: opacity .3s; pointer-events: none; z-index: 999; }
  #toast.show { opacity: 1; }
  #toast.ok  { background: #276749; color: #9ae6b4; }
  #toast.err { background: #9b2c2c; color: #fed7d7; }

  /* modal */
  .overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,.6);
             align-items: center; justify-content: center; z-index: 100; }
  .overlay.open { display: flex; }
  .modal { background: #1a1d27; border: 1px solid #2d3748; border-radius: 10px;
           padding: 24px; width: 340px; }
  .modal h3 { font-size: 15px; margin-bottom: 16px; }
  .modal-actions { display: flex; gap: 10px; justify-content: flex-end; margin-top: 16px; }
  .btn-ghost { background: #2d3748; color: #e2e8f0; }

  #error-msg { color: #fc8181; font-size: 13px; margin-top: 8px; min-height: 18px; }
</style>
</head>
<body>

<!-- Login -->
<div id="login-screen">
  <div class="card" id="login-card">
    <h2 style="margin-bottom:20px;font-size:16px;color:#f7fafc">Admin Login</h2>
    <div class="field">
      <label>Admin Secret</label>
      <input type="password" id="secret-input" placeholder="ADMIN_SECRET from .env"
             onkeydown="if(event.key==='Enter') doLogin()">
    </div>
    <div id="error-msg"></div>
    <button class="btn btn-primary" style="width:100%;margin-top:8px" onclick="doLogin()">Sign In</button>
  </div>
</div>

<!-- Dashboard -->
<div id="dashboard" style="display:none">
  <header>
    <h1>StocksBreakout Admin</h1>
    <span>Users</span>
    <div style="flex:1"></div>
    <button class="btn btn-ghost btn-sm" onclick="signOut()">Sign Out</button>
  </header>
  <main>
    <!-- Add user -->
    <div class="card">
      <h2>Add User</h2>
      <div class="form-row">
        <div class="field">
          <label>Email</label>
          <input type="email" id="new-email" placeholder="user@example.com">
        </div>
        <div class="field">
          <label>Name</label>
          <input type="text" id="new-name" placeholder="Alice">
        </div>
        <div class="field">
          <label>Password (optional)</label>
          <input type="password" id="new-password" placeholder="leave blank = Google only">
        </div>
        <button class="btn btn-primary" style="margin-bottom:1px" onclick="addUser()">Add</button>
      </div>
    </div>

    <!-- Users table -->
    <div class="card">
      <h2>Users <span id="user-count" style="font-size:11px;font-weight:400;color:#718096"></span></h2>
      <table>
        <thead>
          <tr>
            <th>Email</th>
            <th>Name</th>
            <th>Auth</th>
            <th>Created</th>
            <th>Last Login</th>
            <th></th>
          </tr>
        </thead>
        <tbody id="users-tbody">
          <tr><td colspan="6" style="color:#718096;text-align:center;padding:24px">Loading…</td></tr>
        </tbody>
      </table>
    </div>
  </main>
</div>

<!-- Reset password modal -->
<div class="overlay" id="pw-modal">
  <div class="modal">
    <h3>Reset Password</h3>
    <div class="field">
      <label>New Password</label>
      <input type="password" id="modal-pw" placeholder="new password"
             onkeydown="if(event.key==='Enter') doResetPw()">
    </div>
    <div class="modal-actions">
      <button class="btn btn-ghost" onclick="closePwModal()">Cancel</button>
      <button class="btn btn-primary" onclick="doResetPw()">Save</button>
    </div>
  </div>
</div>

<div id="toast"></div>

<script>
let SECRET = '';
let PW_TARGET_ID = '';

function doLogin() {
  const s = document.getElementById('secret-input').value.trim();
  if (!s) return;
  SECRET = s;
  fetch('/admin/users', { headers: { 'X-Admin-Secret': SECRET } })
    .then(r => {
      if (r.status === 403) throw new Error('Wrong secret');
      if (!r.ok) throw new Error('Server error');
      return r.json();
    })
    .then(users => {
      document.getElementById('login-screen').style.display = 'none';
      document.getElementById('dashboard').style.display = 'block';
      renderUsers(users);
    })
    .catch(e => {
      document.getElementById('error-msg').textContent = e.message;
    });
}

function signOut() {
  SECRET = '';
  document.getElementById('dashboard').style.display = 'none';
  document.getElementById('login-screen').style.display = 'block';
  document.getElementById('secret-input').value = '';
}

function loadUsers() {
  fetch('/admin/users', { headers: { 'X-Admin-Secret': SECRET } })
    .then(r => r.json())
    .then(renderUsers)
    .catch(() => toast('Failed to load users', true));
}

function renderUsers(users) {
  const tbody = document.getElementById('users-tbody');
  document.getElementById('user-count').textContent = `(${users.length})`;
  if (!users.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="color:#718096;text-align:center;padding:24px">No users yet</td></tr>';
    return;
  }
  const defaultId = users.find(u => u.email === 'gil.hadas@gmail.com')?.id;
  tbody.innerHTML = users.map(u => `
    <tr>
      <td class="email">${esc(u.email)}</td>
      <td>${esc(u.name)}</td>
      <td>${badgeFor(u.auth)}</td>
      <td class="mono">${esc(u.created_at)}</td>
      <td class="mono">${esc(u.last_login)}</td>
      <td style="text-align:right;white-space:nowrap">
        <button class="btn btn-ghost btn-sm" style="margin-right:6px"
                onclick="openPwModal('${esc(u.id)}')">Reset PW</button>
        ${u.id !== defaultId ? `<button class="btn btn-danger btn-sm"
                onclick="deleteUser('${esc(u.id)}','${esc(u.email)}')">Delete</button>` : ''}
      </td>
    </tr>
  `).join('');
}

function badgeFor(auth) {
  if (auth === 'password+google') return '<span class="badge badge-pg">PW + Google</span>';
  if (auth === 'google')          return '<span class="badge badge-g">Google</span>';
  if (auth === 'password')        return '<span class="badge badge-pw">Password</span>';
  return '<span class="badge badge-none">none</span>';
}

function addUser() {
  const email    = document.getElementById('new-email').value.trim();
  const name     = document.getElementById('new-name').value.trim();
  const password = document.getElementById('new-password').value;
  if (!email) { toast('Email is required', true); return; }
  fetch('/admin/users', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Admin-Secret': SECRET },
    body: JSON.stringify({ email, name, password }),
  })
  .then(r => r.json().then(d => ({ ok: r.ok, d })))
  .then(({ ok, d }) => {
    if (!ok) throw new Error(d.detail || 'Error');
    document.getElementById('new-email').value = '';
    document.getElementById('new-name').value = '';
    document.getElementById('new-password').value = '';
    toast('User created');
    loadUsers();
  })
  .catch(e => toast(e.message, true));
}

function deleteUser(id, email) {
  if (!confirm(`Delete ${email}?`)) return;
  fetch(`/admin/users/${id}`, {
    method: 'DELETE',
    headers: { 'X-Admin-Secret': SECRET },
  })
  .then(r => r.json().then(d => ({ ok: r.ok, d })))
  .then(({ ok, d }) => {
    if (!ok) throw new Error(d.detail || 'Error');
    toast('User deleted');
    loadUsers();
  })
  .catch(e => toast(e.message, true));
}

function openPwModal(userId) {
  PW_TARGET_ID = userId;
  document.getElementById('modal-pw').value = '';
  document.getElementById('pw-modal').classList.add('open');
  setTimeout(() => document.getElementById('modal-pw').focus(), 50);
}

function closePwModal() {
  document.getElementById('pw-modal').classList.remove('open');
  PW_TARGET_ID = '';
}

function doResetPw() {
  const pw = document.getElementById('modal-pw').value;
  if (!pw) { toast('Password cannot be empty', true); return; }
  fetch(`/admin/users/${PW_TARGET_ID}/password`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json', 'X-Admin-Secret': SECRET },
    body: JSON.stringify({ password: pw }),
  })
  .then(r => r.json().then(d => ({ ok: r.ok, d })))
  .then(({ ok, d }) => {
    if (!ok) throw new Error(d.detail || 'Error');
    closePwModal();
    toast('Password updated');
  })
  .catch(e => toast(e.message, true));
}

let _toastTimer;
function toast(msg, err = false) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `show ${err ? 'err' : 'ok'}`;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove('show'), 3000);
}

function esc(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

document.getElementById('secret-input').focus();
</script>
</body>
</html>
"""


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def admin_dashboard():
    return HTMLResponse(_HTML)
