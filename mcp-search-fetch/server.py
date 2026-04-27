"""
MCP Server for Search & Fetch (SearXNG + Trafilatura)
Provides web search capabilities via SearXNG and page fetching via Trafilatura.
Transport: SSE (Server-Sent Events) — compatible with llama.cpp server.
"""

import logging
import sys
import time
import uuid
from datetime import datetime
from typing import Annotated, Optional

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.middleware.cors import CORSMiddleware

from config import settings
from logger import setup_logging, log_request, log_response, log_error

# ── Logging setup ────────────────────────────────────────────────────────────
setup_logging(settings.log_level, settings.log_file)
logger = logging.getLogger(__name__)

# ── FastMCP instance ──────────────────────────────────────────────────────────
from contextlib import asynccontextmanager
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware

mcp = FastMCP(
    "Web Search & Fetch",
    host=settings.mcp_host,
    port=settings.mcp_port,
)

# Streamable HTTP requires its session manager to be executed via lifespan
@asynccontextmanager
async def lifespan(app: Starlette):
    async with mcp.session_manager.run():
        yield

# Wrap it in a parent Starlette app mounted at the root ("/")
app = Starlette(
    lifespan=lifespan,
    routes=[
        # Mount at root to prevent Starlette from doing 307 redirects and stripping paths
        Mount("/", app=mcp.streamable_http_app())
    ],
    middleware=[
        # Apply CORS cleanly at the top-level application
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["mcp-session-id"],
        )
    ]
)

logger.info(
    "MCP Search & Fetch server initialising — host=%s port=%d searxng=%s",
    settings.mcp_host,
    settings.mcp_port,
    settings.searxng_base_url,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_searxng_params(
    query: str,
    categories: Optional[str],
    engines: Optional[str],
    language: str,
    time_range: Optional[str],
    safesearch: int,
    page: int,
) -> dict:
    """Build the query-parameter dict for the SearXNG /search endpoint."""
    params: dict = {
        "q": query,
        "format": "json",
        "language": language,
        "safesearch": safesearch,
        "pageno": page,
    }
    if categories:
        params["categories"] = categories
    if engines:
        params["engines"] = engines
    if time_range:
        params["time_range"] = time_range
    return params


def _build_retry_curl(url: str, params: dict) -> str:
    """Return a cURL command so the search can be repeated manually."""
    from urllib.parse import urlencode
    qs = urlencode(params)
    return f'curl -s "{url}?{qs}" | python -m json.tool'


async def _do_search(
    query: str,
    categories: Optional[str] = None,
    engines: Optional[str] = None,
    language: str = "en",
    time_range: Optional[str] = None,
    safesearch: int = 0,
    page: int = 1,
    max_results: int = 10,
) -> dict:
    """Core async search function — called by all tool variants."""
    request_id = str(uuid.uuid4())[:8]
    search_url = f"{settings.searxng_base_url.rstrip('/')}/search"
    params = _build_searxng_params(query, categories, engines, language, time_range, safesearch, page)

    retry_curl = _build_retry_curl(search_url, params)
    log_request(logger, request_id, query, params, retry_curl)

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            response = await client.get(search_url, params=params)
            response.raise_for_status()
            data = response.json()

        elapsed = time.monotonic() - start
        results = data.get("results", [])[:max_results]
        log_response(logger, request_id, query, f"results={len(results)}", elapsed)

        return {
            "request_id": request_id,
            "query": query,
            "total_found": len(data.get("results", [])),
            "returned": len(results),
            "page": page,
            "results": [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "content": r.get("content", ""),
                    "engine": r.get("engine", ""),
                    "score": r.get("score"),
                    "published_date": r.get("publishedDate"),
                }
                for r in results
            ],
        }

    except httpx.TimeoutException as exc:
        elapsed = time.monotonic() - start
        log_error(logger, request_id, query, "TIMEOUT", str(exc), elapsed, retry_curl)
        return {
            "request_id": request_id,
            "error": "timeout",
            "message": f"SearXNG request timed out after {settings.request_timeout}s",
        }
    except httpx.HTTPStatusError as exc:
        elapsed = time.monotonic() - start
        log_error(logger, request_id, query, f"HTTP {exc.response.status_code}", str(exc), elapsed, retry_curl)
        return {
            "request_id": request_id,
            "error": "http_error",
            "status_code": exc.response.status_code,
            "message": str(exc),
        }
    except Exception as exc:
        elapsed = time.monotonic() - start
        log_error(logger, request_id, query, "UNEXPECTED", str(exc), elapsed, retry_curl)
        return {
            "request_id": request_id,
            "error": "unexpected",
            "message": str(exc),
        }


async def _fetch_website_content(
    url: str,
    output_mode: str = "both",
    output_format: str = "txt",
    max_redirects: int = 5,
    timeout: int = 30,
) -> dict:
    """
    Fetch and extract content from a URL using trafilatura.

    output_mode : 'text' | 'metadata' | 'both'
    output_format: 'txt' | 'markdown' | 'csv' | 'json'
    """
    import trafilatura

    request_id = str(uuid.uuid4())[:8]
    logger.info("Fetching website content [mode=%s fmt=%s]: %s", output_mode, output_format, url)

    start = time.monotonic()
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; MCP-SearXNG/1.0)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
        }

        async with httpx.AsyncClient(
            timeout=timeout,
            headers=headers,
            follow_redirects=True,
            max_redirects=max_redirects,
            verify=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()

        elapsed = time.monotonic() - start
        raw_html = response.text

        # ── trafilatura extraction ────────────────────────────────────────────
        # Map our format names to trafilatura's output_format argument
        _fmt_map = {
            "txt": "txt",
            "markdown": "markdown",
            "csv": "csv",
            "json": "json",
        }
        traf_fmt = _fmt_map.get(output_format, "txt")

        extracted_raw: Optional[str] = None
        metadata_dict: Optional[dict] = None

        # Extract main text content (skip for metadata-only mode)
        if output_mode in ("text", "both"):
            extracted_raw = trafilatura.extract(
                raw_html,
                url=url,
                output_format=traf_fmt,
                with_metadata=False,
                include_comments=False,
                favor_precision=True,
            )

        # Extract structured metadata (skip for text-only mode)
        if output_mode in ("metadata", "both"):
            meta = trafilatura.extract_metadata(raw_html, default_url=url)
            if meta:
                metadata_dict = meta.as_dict()

        # Build the result payload
        result: dict = {
            "request_id": request_id,
            "url": url,
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type", ""),
            "elapsed_seconds": round(elapsed, 3),
            "output_mode": output_mode,
            "output_format": output_format,
            "success": True,
        }

        if output_mode == "metadata":
            result["metadata"] = metadata_dict
        elif output_mode == "text":
            result["content"] = extracted_raw or ""
        else:  # both
            result["content"] = extracted_raw or ""
            result["metadata"] = metadata_dict

        log_response(logger, request_id, url, f"status={response.status_code}", elapsed)
        return result

    except httpx.HTTPStatusError as exc:
        elapsed = time.monotonic() - start
        log_error(logger, request_id, url, f"HTTP {exc.response.status_code}", str(exc), elapsed)
        return {
            "request_id": request_id,
            "url": url,
            "error": "http_error",
            "status_code": exc.response.status_code,
            "message": str(exc),
            "elapsed_seconds": round(elapsed, 3),
        }
    except httpx.TimeoutException:
        elapsed = time.monotonic() - start
        log_error(logger, request_id, url, "TIMEOUT", "Request timed out", elapsed)
        return {
            "request_id": request_id,
            "url": url,
            "error": "timeout",
            "message": f"Request timed out after {timeout}s",
            "elapsed_seconds": round(elapsed, 3),
        }
    except httpx.HTTPError as exc:
        elapsed = time.monotonic() - start
        log_error(logger, request_id, url, "HTTP_ERROR", str(exc), elapsed)
        return {
            "request_id": request_id,
            "url": url,
            "error": "http_error",
            "message": str(exc),
            "elapsed_seconds": round(elapsed, 3),
        }
    except Exception as exc:
        elapsed = time.monotonic() - start
        log_error(logger, request_id, url, "UNEXPECTED", str(exc), elapsed)
        return {
            "request_id": request_id,
            "url": url,
            "error": "unexpected",
            "message": str(exc),
            "elapsed_seconds": round(elapsed, 3),
        }


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
async def web_search(
    query: Annotated[str, "The search query to look up on the web"],
    max_results: Annotated[int, "Maximum number of results to return (1–20)"] = 10,
    language: Annotated[str, "Language code for results, e.g. 'en', 'fr'"] = "en",
    time_range: Annotated[
        Optional[str],
        "Restrict results to a time range: 'day', 'week', 'month', 'year', or omit for any time",
    ] = None,
) -> dict:
    """
    Search the web using SearXNG. Returns titles, URLs, and snippets for the
    top results. Use this for general web searches.
    """
    return await _do_search(
        query=query,
        categories="general",
        language=language,
        time_range=time_range,
        max_results=min(max(1, max_results), 20),
    )


@mcp.tool()
async def news_search(
    query: Annotated[str, "The news query to search for"],
    max_results: Annotated[int, "Maximum number of results to return (1–20)"] = 10,
    time_range: Annotated[
        Optional[str],
        "Restrict results to a time range: 'day', 'week', 'month', 'year', or omit for any time",
    ] = "week",
    language: Annotated[str, "Language code for results, e.g. 'en', 'fr'"] = "en",
) -> dict:
    """
    Search for news articles using SearXNG's news category.
    Defaults to the past week for freshness. Returns titles, URLs, and snippets.
    """
    return await _do_search(
        query=query,
        categories="news",
        language=language,
        time_range=time_range,
        max_results=min(max(1, max_results), 20),
    )


@mcp.tool()
async def advanced_search(
    query: Annotated[str, "The search query"],
    categories: Annotated[
        Optional[str],
        "Comma-separated SearXNG category names, e.g. 'general,news,science'",
    ] = None,
    engines: Annotated[
        Optional[str],
        "Comma-separated engine names to force, e.g. 'google,bing,duckduckgo'",
    ] = None,
    language: Annotated[str, "Language code for results"] = "en",
    time_range: Annotated[
        Optional[str],
        "Time range: 'day', 'week', 'month', 'year', or omit for any",
    ] = None,
    safesearch: Annotated[int, "Safe-search level: 0=off, 1=moderate, 2=strict"] = 0,
    page: Annotated[int, "Results page number (1-based)"] = 1,
    max_results: Annotated[int, "Maximum number of results to return (1–20)"] = 10,
) -> dict:
    """
    Advanced SearXNG search with full control over categories, engines,
    safe-search, and pagination.
    """
    return await _do_search(
        query=query,
        categories=categories,
        engines=engines,
        language=language,
        time_range=time_range,
        safesearch=safesearch,
        page=page,
        max_results=min(max(1, max_results), 20),
    )


@mcp.tool()
async def searxng_status() -> dict:
    """
    Check connectivity to the configured SearXNG instance.
    Returns version info and engine availability if accessible.
    """
    status_url = f"{settings.searxng_base_url.rstrip('/')}/config"
    logger.info("Checking SearXNG status at %s", status_url)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(status_url)
            response.raise_for_status()
            data = response.json()
        engines = list(data.get("engines", {}).keys())
        logger.info("SearXNG status OK — %d engines available", len(engines))
        return {
            "status": "ok",
            "searxng_url": settings.searxng_base_url,
            "version": data.get("version"),
            "engines_available": len(engines),
            "engines": engines[:30],  # cap to avoid flooding context window
        }
    except Exception as exc:
        logger.warning("SearXNG status check failed: %s", exc)
        return {
            "status": "error",
            "searxng_url": settings.searxng_base_url,
            "message": str(exc),
        }


@mcp.tool()
async def fetch_website_content(
    url: Annotated[str, "The URL of the website to fetch content from"],
    output_mode: Annotated[
        str,
        "What to return: 'text' (main article text only), 'metadata' (title, author, date, etc.), or 'both' (default)",
    ] = "both",
    output_format: Annotated[
        str,
        "Output format: 'txt' (plain text, default), 'markdown', 'csv', or 'json'",
    ] = "txt",
    max_redirects: Annotated[
        int,
        "Maximum number of redirects to follow (default: 5)",
    ] = 5,
    timeout: Annotated[
        int,
        "Request timeout in seconds (default: 30)",
    ] = 30,
) -> dict:
    """
    Fetch and extract content from a URL using trafilatura.

    Uses trafilatura for high-quality main-content extraction (removes navigation,
    ads, boilerplate, etc.).

    Args:
        url: The URL of the website to fetch
        output_mode: Controls what is returned —
            'text'     → extracted main text only
            'metadata' → structured metadata only (title, author, date, description,
                         sitename, tags, language, …)
            'both'     → text + metadata (default)
        output_format: Serialisation format —
            'txt'      → plain text (default)
            'markdown' → Markdown with headings and links preserved
            'csv'      → CSV with one field per row
            'json'     → JSON object (best combined with output_mode='both')
        max_redirects: Maximum redirects to follow (default: 5)
        timeout: Request timeout in seconds (default: 30)

    Returns:
        Dictionary containing:
        - url, status_code, content_type, elapsed_seconds, output_mode, output_format
        - content : extracted text/structured content (when output_mode is 'text' or 'both')
        - metadata: dict of page metadata (when output_mode is 'metadata' or 'both')
        - success : True on successful fetch

    Example:
        Fetch Wikipedia article as markdown with metadata:
        fetch_website_content(
            url="https://en.wikipedia.org/wiki/Python_(programming_language)",
            output_mode="both",
            output_format="markdown"
        )
    """
    # Validate URL
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return {
                "url": url,
                "error": "invalid_url",
                "message": "Invalid URL format. Please provide a valid HTTP/HTTPS URL.",
            }
    except Exception as exc:
        return {
            "url": url,
            "error": "validation_error",
            "message": f"URL validation failed: {str(exc)}",
        }

    # Validate parameter values
    valid_modes = {"text", "metadata", "both"}
    valid_formats = {"txt", "markdown", "csv", "json"}
    if output_mode not in valid_modes:
        return {
            "url": url,
            "error": "invalid_parameter",
            "message": f"output_mode must be one of {sorted(valid_modes)}, got '{output_mode}'.",
        }
    if output_format not in valid_formats:
        return {
            "url": url,
            "error": "invalid_parameter",
            "message": f"output_format must be one of {sorted(valid_formats)}, got '{output_format}'.",
        }

    return await _fetch_website_content(
        url=url,
        output_mode=output_mode,
        output_format=output_format,
        max_redirects=max_redirects,
        timeout=timeout,
    )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    
    logger.info(
        "Starting MCP Search & Fetch SSE server on http://%s:%d/sse",
        settings.mcp_host,
        settings.mcp_port,
    )
    
    # Run the middleware-wrapped ASGI app directly via Uvicorn
    uvicorn.run(app, host=settings.mcp_host, port=settings.mcp_port)
