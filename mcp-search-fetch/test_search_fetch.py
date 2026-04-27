#!/usr/bin/env python3
"""
Standalone CLI tool to test the SearXNG connection and run ad-hoc searches
without starting the MCP server.

Usage:
    python test_search_fetch.py "python async programming"
    python test_search_fetch.py "AI news" --categories news --time-range day
    python test_search_fetch.py --status
"""

import argparse
import asyncio
import json
import sys

# Reuse the server's config and core search logic
from config import settings
from logger import setup_logging
from server import _do_search, searxng_status, _fetch_website_content


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test SearXNG connectivity and searches from the command line."
    )
    parser.add_argument("query", nargs="?", help="Search query")
    parser.add_argument("--status", action="store_true", help="Check SearXNG status only")
    parser.add_argument("--fetch", help="Fetch and extract content from a URL")
    parser.add_argument(
        "--mode",
        choices=["text", "metadata", "both"],
        default="both",
        help="Fetch mode (default: both)",
    )
    parser.add_argument(
        "--format",
        choices=["txt", "markdown", "csv", "json"],
        default="txt",
        help="Fetch output format (default: txt)",
    )
    parser.add_argument("--categories", default=None, help="SearXNG categories (comma-separated)")
    parser.add_argument("--engines", default=None, help="Force specific engines (comma-separated)")
    parser.add_argument("--language", default="en", help="Language code (default: en)")
    parser.add_argument(
        "--time-range",
        default=None,
        choices=["day", "week", "month", "year"],
        help="Time range filter",
    )
    parser.add_argument("--safesearch", type=int, default=0, choices=[0, 1, 2])
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--max-results", type=int, default=5)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--json", action="store_true", help="Output raw JSON response")

    args = parser.parse_args()

    setup_logging(args.log_level)

    if args.status:
        result = await searxng_status()
        print(json.dumps(result, indent=2))
        return

    if args.fetch:
        result = await _fetch_website_content(
            url=args.fetch,
            output_mode=args.mode,
            output_format=args.format,
        )
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if not result.get("success"):
                print(f"\n❌  Error: {result.get('message')}")
                sys.exit(1)

            print(f"\n✅  Fetched: {result['url']}")
            print(f"    Status: {result['status_code']} | Time: {result['elapsed_seconds']}s\n")
            
            if "metadata" in result and result["metadata"]:
                print("── Metadata ──────────────────────────────────────────────────")
                for k, v in result["metadata"].items():
                    if v:
                        print(f"  {k:12}: {v}")
                print()

            if "content" in result and result["content"]:
                print("── Content ───────────────────────────────────────────────────")
                # Show first 1000 chars of content
                content = result["content"]
                if len(content) > 1000:
                    print(content[:1000] + "...")
                else:
                    print(content)
                print()
        return

    if not args.query:
        parser.error("Provide a query, use --status, or use --fetch")

    result = await _do_search(
        query=args.query,
        categories=args.categories,
        engines=args.engines,
        language=args.language,
        time_range=args.time_range,
        safesearch=args.safesearch,
        page=args.page,
        max_results=args.max_results,
    )

    if args.json:
        print(json.dumps(result, indent=2))
        return

    if "error" in result:
        print(f"\n❌  Error: {result.get('message')}")
        print("\nℹ️  The retry cURL command is available in the MCP server logs.")
        sys.exit(1)

    print(f"\n✅  {result['returned']} results for: {result['query']!r}\n")
    for i, r in enumerate(result["results"], 1):
        print(f"  {i}. {r['title']}")
        print(f"     {r['url']}")
        if r["content"]:
            snippet = r["content"][:160].replace("\n", " ")
            print(f"     {snippet}…")
        print()

    print("ℹ️  The retry cURL command is available in the MCP server logs.\n")


if __name__ == "__main__":
    asyncio.run(main())
