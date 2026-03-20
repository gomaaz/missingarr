from fastapi import APIRouter
from backend.config import settings

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok", "version": settings.version, "app": settings.app_name}
