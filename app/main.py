"""
AutoClipper Professional SaaS
FastAPI + Premium UI
"""
import os
import secrets
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request, Form, Depends, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.database import (
    add_clip, get_user_clips, create_job_record,
    get_setting, set_setting, get_all_settings, create_or_get_user,
    get_user, use_tokens, add_tokens, add_waitlist, get_waitlist, init_db
)
from app.clipper_core import run_job
from app.live_monitor import start_live, stop_live

BASE = Path(__file__).parent.parent
app = FastAPI(title="AutoClipper")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY", secrets.token_hex(32)), max_age=7200)

app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

# Admin password (change in production)
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")



def require_login(request: Request):
    user = get_current_user(request)
    if not user:
        return None
    return user

def get_current_user(request: Request) -> Optional[dict]:
    uid = request.session.get("user_id")
    if uid:
        return get_user(uid)
    return None


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    settings = get_all_settings()
    user = get_current_user(request)
    prelaunch = settings.get("prelaunch_mode", "true") == "true"
    template = "waitlist.html" if prelaunch else "index.html"
    return templates.TemplateResponse(template, {
        "request": request,
        "settings": settings,
        "user": user,
        "prelaunch": prelaunch,
    })


@app.post("/waitlist")
async def join_waitlist(email: str = Form(...), name: str = Form("")):
    add_waitlist(email, name)
    return RedirectResponse("/?joined=1", status_code=303)


@app.get("/login")
async def login_page(request: Request):
    # Google OAuth placeholder - in production use Authlib + Google client
    return templates.TemplateResponse("login.html", {"request": request})



@app.get("/login/google")
async def google_login(request: Request):
    # Placeholder - in production use Authlib + Google OAuth
    # For now redirects to demo login
    return RedirectResponse("/login?google=1")

@app.post("/login/demo")
async def demo_login(request: Request, email: str = Form(...)):
    """Demo login for testing (replace with real Google OAuth)"""
    user = create_or_get_user(email, name=email.split("@")[0].title())
    request.session["user_id"] = user["id"]
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    settings = get_all_settings()
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "settings": settings,
    })


@app.post("/clip")
async def create_clip(
    request: Request,
    url: str = Form(...),
    clips: int = Form(5),
    lang: str = Form("en"),
    layout: str = Form("crop"),
    captions: str = Form("center"),
    is_live: str = Form("false"),
):
    user = get_current_user(request)
    if not user:
        raise HTTPException(401)

    live = is_live == "on" or is_live == "true"
    if live:
        # Start live monitor (tokens deducted per cycle inside monitor)
        out_dir = BASE / "clips" / f"user_{user['id']}" / f"live_{int(datetime.utcnow().timestamp())}"
        job_id = int(datetime.utcnow().timestamp())
        start_live(
            job_id=job_id,
            user_id=user["id"],
            url=url,
            out_dir=out_dir,
            clips_per_cycle=max(2, clips // 2),
            layout=layout,
            caption_lang=lang,
        )
        return RedirectResponse("/processing", status_code=303)

    # Normal finished video
    cost = clips * 8
    if not use_tokens(user["id"], cost):
        return RedirectResponse("/dashboard?error=notokens", status_code=303)

    try:
        out_dir = BASE / "clips" / f"user_{user['id']}"
        out_dir.mkdir(parents=True, exist_ok=True)
        results = run_job(url, clips=clips, caption_lang=lang, layout=layout, out_dir=out_dir)
        job_id = create_job_record(user["id"], url)
        for r in results:
            add_clip(job_id, r.get("file", ""), r.get("title", ""), r.get("start", 0), r.get("end", 0))
        request.session["last_job"] = {"clips": len(results), "url": url}
        return RedirectResponse("/processing", status_code=303)
    except Exception as e:
        print(f"Job error: {e}")
        add_tokens(user["id"], cost)
        return RedirectResponse(f"/dashboard?error=jobfailed&msg={str(e)[:100]}", status_code=303)






@app.get("/referral", response_class=HTMLResponse)
async def referral_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    settings = get_all_settings()
    return templates.TemplateResponse("referral.html", {"request": request, "user": user, "settings": settings})

@app.get("/coupons", response_class=HTMLResponse)
async def coupons_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse("coupons.html", {"request": request, "user": user})

@app.post("/redeem-coupon")
async def redeem_coupon(request: Request, code: str = Form(...)):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    # Simple demo: any code gives 50 tokens for now
    add_tokens(user["id"], 50)
    return RedirectResponse("/coupons?success=1", status_code=303)

@app.get("/billing", response_class=HTMLResponse)
async def billing(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    settings = get_all_settings()
    return templates.TemplateResponse("billing.html", {"request": request, "user": user, "settings": settings})


@app.get("/download/{clip_id}")
async def download_clip(clip_id: int, request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    conn = __import__("app.database", fromlist=["get_conn"]).get_conn()
    c = conn.cursor()
    c.execute("SELECT file_path FROM clips WHERE id = ?", (clip_id,))
    row = c.fetchone()
    conn.close()
    if not row or not row["file_path"]:
        raise HTTPException(404)
    path = Path(row["file_path"])
    if not path.exists():
        raise HTTPException(404, "File not found on disk")
    return FileResponse(path, filename=path.name, media_type="video/mp4")

@app.get("/clips", response_class=HTMLResponse)
async def my_clips(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    clips = get_user_clips(user["id"])
    return templates.TemplateResponse("clips.html", {"request": request, "user": user, "clips": clips})

@app.get("/processing", response_class=HTMLResponse)
async def processing(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse("processing.html", {"request": request, "user": user})

@app.get("/admin", response_class=HTMLResponse)
async def admin_login(request: Request):
    if request.session.get("is_admin"):
        return RedirectResponse("/admin/dashboard")
    return templates.TemplateResponse("admin_login.html", {"request": request})


@app.post("/admin/login")
async def admin_auth(request: Request, password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        request.session["is_admin"] = True
        return RedirectResponse("/admin/dashboard", status_code=303)
    return templates.TemplateResponse("admin_login.html", {"request": request, "error": "Wrong password"})


@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dash(request: Request):
    if not request.session.get("is_admin"):
        return RedirectResponse("/admin")
    settings = get_all_settings()
    waitlist = get_waitlist()
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "settings": settings,
        "waitlist": waitlist,
    })



@app.post("/admin/upload-banner")
async def upload_banner(request: Request, file: UploadFile = File(...)):
    if not request.session.get("is_admin"):
        raise HTTPException(403)
    upload_dir = BASE / "static" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename).suffix or ".jpg"
    filename = f"banner_{int(__import__('time').time())}{ext}"
    path = upload_dir / filename
    with open(path, "wb") as f:
        f.write(await file.read())
    set_setting("banner_image", f"/static/uploads/{filename}")
    return RedirectResponse("/admin/dashboard?saved=1", status_code=303)

@app.post("/admin/settings")
async def update_settings(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(403)
    form = await request.form()
    keys = [
        "prelaunch_mode", "banner_active", "banner_text", "hero_title", "hero_subtitle",
        "waitlist_headline", "waitlist_subtext",
        "pricing_free_tokens", "pricing_pro_monthly", "pricing_pro_6month", "pricing_pro_yearly", "pricing_pro_monthly_usd", "pricing_pro_6month_usd", "pricing_pro_yearly_usd",
        "referral_enabled", "referral_bonus_tokens", "referral_button_text",
        "banner_image", "banner_button", "banner_link",
    ]
    for k in keys:
        val = form.get(k, "")
        if k in ("prelaunch_mode", "banner_active", "referral_enabled"):
            val = "true" if val == "on" else "false"
        set_setting(k, str(val) if val is not None else "")
    return RedirectResponse("/admin/dashboard?saved=1", status_code=303)



@app.get("/debug/settings")
async def debug_settings():
    return get_all_settings()

@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}
