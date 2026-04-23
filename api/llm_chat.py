"""
Claude wrapper for the Analyze chat. Keeps the LLM grounded on the scanner
report by pinning it as system context.
"""

import json
import os
from typing import List, Dict

_SYSTEM_PREAMBLE = """You are a senior quantitative trader explaining a single-stock technical report produced by a breakout scanner.

Ground every answer in the report JSON below. Do not invent indicator values, prices, or patterns. If the report does not contain enough information to answer, say so. Cite specific fields (Quality, R:R, RSI, Patterns, Checks, etc.) by name when you reference them.

Be concise: 3-6 sentences unless the user asks for more. Focus on what matters for a trading decision — setup quality, risk/reward, trend alignment, and the biggest risk."""


async def chat(report: Dict, history: List[Dict], question: str) -> str:
    api_key = os.getenv('ANTHROPIC_API_KEY')
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    try:
        from anthropic import AsyncAnthropic
    except ImportError as e:
        raise RuntimeError(f"anthropic SDK not installed: {e}")

    client = AsyncAnthropic(api_key=api_key)

    system = [
        {
            "type": "text",
            "text": _SYSTEM_PREAMBLE,
        },
        {
            "type": "text",
            "text": "Report JSON:\n" + json.dumps(report, default=str, indent=2),
            "cache_control": {"type": "ephemeral"},
        },
    ]

    messages: List[Dict] = []
    for turn in history:
        role = turn.get('role')
        content = turn.get('content', '')
        if role in ('user', 'assistant') and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})

    resp = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=system,
        messages=messages,
    )

    parts = [b.text for b in resp.content if getattr(b, 'type', None) == 'text']
    return "\n".join(parts).strip()
