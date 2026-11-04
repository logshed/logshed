"""Serving the built frontend: precompressed files, caching headers and conditional requests.

The frontend build writes a Brotli (`.br`) and a gzip (`.gz`) copy next to each text file
(`frontend/vite.config.ts`). A request whose Accept-Encoding allows one of them gets that copy,
with the original file's content type. Nothing is compressed at request time, so API responses
and the SSE streams are never compressed or buffered.
"""

import mimetypes
import os
import stat
from email.utils import parsedate
from pathlib import Path
from typing import Union

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers
from starlette.responses import FileResponse, Response
from starlette.staticfiles import NotModifiedResponse
from starlette.types import Scope

# Vite puts a content hash in every file name under /assets/, so a changed file gets a new URL
IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
# index.html and the other unhashed files: always ask the server, so a new release loads at once
REVALIDATE_CACHE = "no-cache"

# Preferred first
_COMPRESSED_VARIANTS = (("br", ".br"), ("gzip", ".gz"))

PathLike = Union[str, "os.PathLike[str]"]


def accepted_encodings(accept_encoding: str) -> set[str]:
    """The content codings an Accept-Encoding header allows (q above 0), lower case."""
    allowed: set[str] = set()
    refused: set[str] = set()
    wildcard = False
    for item in accept_encoding.split(","):
        coding, _, params = item.strip().partition(";")
        coding = coding.strip().lower()
        if not coding:
            continue
        quality = 1.0
        for param in params.split(";"):
            name, _, value = param.strip().partition("=")
            if name.strip().lower() == "q":
                try:
                    quality = float(value.strip())
                except ValueError:
                    quality = 0.0
        if coding == "*":
            wildcard = quality > 0
        elif quality > 0:
            allowed.add(coding)
        else:
            refused.add(coding)
    if wildcard:
        allowed.update(c for c, _ in _COMPRESSED_VARIANTS if c not in refused)
    return allowed


def _is_not_modified(response_headers: Headers, request_headers: Headers) -> bool:
    """True when the request's validators match the response (as Starlette's StaticFiles checks)."""
    if if_none_match := request_headers.get("if-none-match"):
        if if_none_match.strip() == "*":
            return True
        etag = response_headers["etag"]
        return etag in [tag.strip().removeprefix("W/") for tag in if_none_match.split(",")]
    if_modified_since = parsedate(request_headers.get("if-modified-since", ""))
    last_modified = parsedate(response_headers.get("last-modified", ""))
    return if_modified_since is not None and last_modified is not None and if_modified_since >= last_modified


def _regular_file_stat(path: str) -> os.stat_result | None:
    try:
        result = os.stat(path)
    except OSError:
        return None
    return result if stat.S_ISREG(result.st_mode) else None


def static_file_response(
    full_path: PathLike,
    stat_result: os.stat_result,
    request_headers: Headers,
    cache_control: str,
    status_code: int = 200,
) -> Response:
    """Serve full_path, or its precompressed copy when the client accepts it; answer 304 when unchanged."""
    path = os.fspath(full_path)
    headers = {"Cache-Control": cache_control}
    serve_path, serve_stat = path, stat_result
    variants = [(coding, path + suffix, _regular_file_stat(path + suffix)) for coding, suffix in _COMPRESSED_VARIANTS]
    if any(variant_stat for _, _, variant_stat in variants):
        # Caches must keep the compressed and plain copies apart
        headers["Vary"] = "Accept-Encoding"
        accepted = accepted_encodings(request_headers.get("accept-encoding", ""))
        for coding, variant_path, variant_stat in variants:
            if variant_stat and coding in accepted:
                serve_path, serve_stat = variant_path, variant_stat
                headers["Content-Encoding"] = coding
                break
    media_type = mimetypes.guess_type(path)[0] or "text/plain"
    # The ETag comes from the file actually sent, so each copy has its own
    response = FileResponse(
        serve_path, status_code=status_code, headers=headers, media_type=media_type, stat_result=serve_stat
    )
    if _is_not_modified(response.headers, request_headers):
        return NotModifiedResponse(response.headers)
    return response


class HashedAssets(StaticFiles):
    """/assets/: Vite's content-hashed files, which browsers may keep for a year without asking."""

    def file_response(
        self,
        full_path: PathLike,
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        return static_file_response(full_path, stat_result, Headers(scope=scope), IMMUTABLE_CACHE, status_code)


def mount_frontend(app: FastAPI, static_dir: Path) -> None:
    """Serve the built SPA from static_dir: /assets/, other files by path, index.html for anything else."""
    assets_dir = static_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", HashedAssets(directory=str(assets_dir)), name="assets")
    static_root = static_dir.resolve()

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str, request: Request):
        if full_path.startswith("api") or full_path.startswith("mcp"):
            raise HTTPException(status_code=404, detail="Not Found")
        resolved_file = (static_dir / full_path).resolve()
        if not resolved_file.is_relative_to(static_root):
            raise HTTPException(status_code=404, detail="Not Found")
        file_stat = _regular_file_stat(str(resolved_file))
        if file_stat is None:
            # Frontend routes such as /rules: the SPA's index.html routes them in the browser
            resolved_file = static_dir / "index.html"
            file_stat = _regular_file_stat(str(resolved_file))
            if file_stat is None:
                raise HTTPException(status_code=404, detail="Static files not found")
        return static_file_response(resolved_file, file_stat, request.headers, REVALIDATE_CACHE)
