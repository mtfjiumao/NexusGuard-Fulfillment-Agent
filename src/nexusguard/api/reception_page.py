"""Serve the Vite build; customer authorization remains in the reception APIs."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

WEB_DIRECTORY = Path(__file__).resolve().parents[1] / "web" / "dist"
router = APIRouter()


@router.get("/customer-service", include_in_schema=False)
async def reception_page() -> FileResponse:
    index = WEB_DIRECTORY / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=503, detail="frontend_build_required")
    return FileResponse(
        index,
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; object-src 'none'; base-uri 'none'; "
                "form-action 'self'; frame-ancestors 'self'"
            ),
        },
    )
