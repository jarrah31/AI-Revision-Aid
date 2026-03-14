import re
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pathlib import Path

from backend.database import init_db

BASE_DIR = Path(__file__).parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
DATA_DIR = BASE_DIR / "data"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


APP_VERSION = "1.3.2"

app = FastAPI(title="RevisionAid", version=APP_VERSION, lifespan=lifespan)

# Routers
from backend.routers.auth import router as auth_router
from backend.routers.subjects import router as subjects_router
from backend.routers.upload import router as upload_router
from backend.routers.questions import router as questions_router
from backend.routers.quiz import router as quiz_router
from backend.routers.sharing import router as sharing_router
from backend.routers.admin import router as admin_router
from backend.routers.dashboard import router as dashboard_router
from backend.routers.costs import router as costs_router
from backend.routers.categories import router as categories_router

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])
app.include_router(subjects_router, prefix="/api/subjects", tags=["subjects"])
app.include_router(upload_router, prefix="/api/upload", tags=["upload"])
app.include_router(questions_router, prefix="/api/questions", tags=["questions"])
app.include_router(quiz_router, prefix="/api/quiz", tags=["quiz"])
app.include_router(sharing_router, prefix="/api/shared", tags=["sharing"])
app.include_router(admin_router, prefix="/api/admin", tags=["admin"])
app.include_router(dashboard_router, prefix="/api/dashboard", tags=["dashboard"])
app.include_router(costs_router, prefix="/api/costs", tags=["costs"])
app.include_router(categories_router, prefix="/api/categories", tags=["categories"])

@app.get("/api/version")
async def get_version():
    return {"version": APP_VERSION}


# Serve extracted images
app.mount("/images", StaticFiles(directory=str(DATA_DIR / "images")), name="images")

# Serve stored PDFs
app.mount("/pdfs", StaticFiles(directory=str(DATA_DIR / "pdfs")), name="pdfs")

# Serve frontend static files
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
async def serve_index():
    """Serve index.html with cache-busting version on all local JS/CSS assets."""
    html = (FRONTEND_DIR / "index.html").read_text()
    v = int(time.time())
    # Add ?v=<timestamp> to every /static/js/*.js and /static/css/*.css reference
    html = re.sub(
        r'(src|href)="(/static/(?:js|css)/[^"]+)"',
        lambda m: f'{m.group(1)}="{m.group(2)}?v={v}"',
        html,
    )
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})
