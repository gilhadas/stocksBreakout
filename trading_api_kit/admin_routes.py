"""
trading_api_kit.admin_routes — user management API + dashboard.

All routes require the X-Admin-Secret header matching ADMIN_SECRET in .env.

Routes:
  GET    /admin/users        — list all users
  POST   /admin/users        — create user
  DELETE /admin/users/{id}   — delete user
  PATCH  /admin/users/{id}/password — change password
  GET    /admin              — HTML dashboard

Usage:
    from trading_api_kit.admin_routes import router as admin_router
    app.include_router(admin_router)
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Header
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from trading_api_kit.auth import hash_password
from trading_api_kit.config import ADMIN_SECRET
from trading_api_kit.database import get_db
from trading_api_kit.models import User

router = APIRouter(prefix="/admin", tags=["admin"])


def _require_admin(x_admin_secret: str = Header(default="")):
    if not ADMIN_SECRET or x_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")


# ── Request models ────────────────────────────────────────────────────────────

class CreateUserRequest(BaseModel):
    email: str
    name: str = ""
    password: str = ""


class ChangePasswordRequest(BaseModel):
    password: str


# ── API ───────────────────────────────────────────────────────────────────────

@router.get("/users")
def list_users(db: Session = Depends(get_db), _=Depends(_require_admin)):
    users = db.query(User).order_by(User.created_at).all()
    return [
        {
            "id":         u.id,
            "email":      u.email,
            "name":       u.name or "",
            "auth":       (
                "password+google" if u.password_hash and u.google_id
                else "google" if u.google_id
                else "password" if u.password_hash
                else "none"
            ),
            "created_at": u.created_at.strftime("%Y-%m-%d %H:%M") if u.created_at else "",
            "last_login": u.last_login.strftime("%Y-%m-%d %H:%M") if u.last_login else "never",
        }
        for u in users
    ]


@router.post("/users", status_code=201)
def create_user(
    req: CreateUserRequest,
    db: Session = Depends(get_db),
    _=Depends(_require_admin),
):
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
def delete_user(
    user_id: str,
    db: Session = Depends(get_db),
    _=Depends(_require_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)
    db.commit()
    return {"ok": True}


@router.patch("/users/{user_id}/password")
def change_password(
    user_id: str,
    req: ChangePasswordRequest,
    db: Session = Depends(get_db),
    _=Depends(_require_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.password_hash = hash_password(req.password)
    db.commit()
    return {"ok": True}


# ── HTML dashboard ────────────────────────────────────────────────────────────

@router.get("", response_class=HTMLResponse)
def admin_dashboard(_=Depends(_require_admin)):
    """Minimal HTML admin dashboard for user management."""
    return HTMLResponse(content="""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Admin — User Management</title>
<style>
  body { font-family: system-ui, sans-serif; background: #0f0f23; color: #e0e0e0;
         margin: 0; padding: 24px; }
  h1 { color: #6366f1; }
  table { width: 100%; border-collapse: collapse; margin-top: 16px; }
  th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid #2a2a4a; }
  th { background: #1a1a3e; color: #a0a0ff; }
  tr:hover td { background: #1a1a2e; }
  .badge { padding: 2px 8px; border-radius: 4px; font-size: 12px; }
  .badge-pw   { background: #1e3a5f; color: #60a5fa; }
  .badge-g    { background: #1e3a1e; color: #4ade80; }
  .badge-both { background: #3a1e3a; color: #c084fc; }
  .badge-none { background: #3a1e1e; color: #f87171; }
  button { background: #6366f1; border: none; color: white; padding: 6px 14px;
           border-radius: 6px; cursor: pointer; font-size: 13px; margin: 2px; }
  button.danger { background: #dc2626; }
  input { background: #1a1a2e; color: white; border: 1px solid #444;
          border-radius: 6px; padding: 6px 10px; font-size: 14px; width: 200px; }
  #form { margin-top: 24px; padding: 16px; background: #1a1a2e;
          border-radius: 8px; max-width: 500px; }
  #form h2 { color: #a0a0ff; margin-top: 0; }
  #msg { margin-top: 12px; color: #4ade80; }
</style>
</head>
<body>
<h1>User Management</h1>
<div id="users-table">Loading...</div>

<div id="form">
  <h2>Add User</h2>
  <input id="new-email" placeholder="Email" type="email"><br><br>
  <input id="new-name"  placeholder="Name (optional)"><br><br>
  <input id="new-pass"  placeholder="Password" type="password"><br><br>
  <button onclick="createUser()">Create User</button>
  <div id="msg"></div>
</div>

<script>
const SECRET = prompt("Admin secret:");
const H = { "Content-Type": "application/json", "X-Admin-Secret": SECRET };

async function loadUsers() {
  const r = await fetch("/admin/users", { headers: H });
  if (!r.ok) { document.getElementById("users-table").textContent = "Access denied."; return; }
  const users = await r.json();
  const rows = users.map(u => {
    const badge = u.auth === "password+google" ? `<span class="badge badge-both">pw+google</span>`
                : u.auth === "google"           ? `<span class="badge badge-g">google</span>`
                : u.auth === "password"          ? `<span class="badge badge-pw">password</span>`
                :                                  `<span class="badge badge-none">none</span>`;
    return `<tr>
      <td>${u.email}</td><td>${u.name}</td><td>${badge}</td>
      <td>${u.created_at}</td><td>${u.last_login}</td>
      <td>
        <button class="danger" onclick="deleteUser('${u.id}','${u.email}')">Delete</button>
        <button onclick="changePass('${u.id}')">Change PW</button>
      </td>
    </tr>`;
  }).join("");
  document.getElementById("users-table").innerHTML =
    `<table><tr><th>Email</th><th>Name</th><th>Auth</th><th>Created</th><th>Last Login</th><th></th></tr>${rows}</table>`;
}

async function createUser() {
  const email = document.getElementById("new-email").value;
  const name  = document.getElementById("new-name").value;
  const pass  = document.getElementById("new-pass").value;
  const r = await fetch("/admin/users", { method: "POST", headers: H,
    body: JSON.stringify({ email, name, password: pass }) });
  const msg = document.getElementById("msg");
  if (r.ok) { msg.textContent = "User created!"; msg.style.color="#4ade80"; loadUsers(); }
  else { const e = await r.json(); msg.textContent = e.detail; msg.style.color="#f87171"; }
}

async function deleteUser(id, email) {
  if (!confirm(`Delete ${email}?`)) return;
  await fetch(`/admin/users/${id}`, { method: "DELETE", headers: H });
  loadUsers();
}

async function changePass(id) {
  const pw = prompt("New password:");
  if (!pw) return;
  await fetch(`/admin/users/${id}/password`, { method: "PATCH", headers: H,
    body: JSON.stringify({ password: pw }) });
  alert("Password updated.");
}

loadUsers();
</script>
</body>
</html>
""")
