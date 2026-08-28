"""
backend/core/json_utils.py
Tolerant JSON extraction helpers for LLM responses across J.A.R.V.I.S. subsystems.

Why this exists:
  Planning/observation models sometimes wrap their JSON in prose ("Here is my plan:")
  and providers can emit trailing commentary after the closing brace. Strict
  json.loads() then crashes even though a complete object IS present. These helpers
  locate and decode the FIRST balanced JSON object anywhere in the string.
"""

import json
from typing import Any, Optional

_DECODER = json.JSONDecoder()


def extract_json_object(raw_response: str) -> Optional[Any]:
    """
    Returns the first decodable JSON value found in `raw_response`, or None.

    Handles: raw objects, ```json fenced blocks, leading/trailing prose,
    and surrounding whitespace — without failing on the noise around them.
    """
    if not isinstance(raw_response, str):
        return None

    text = raw_response.strip()
    if not text:
        return None

    # Fast path: entire payload is valid JSON.
    try:
        return json.loads(text)
    except Exception:
        pass

    # Scan for the first '{' that begins a balanced, decodable object.
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            value, _end = _DECODER.raw_decode(text[idx:])
            if isinstance(value, dict):
                return value
        except Exception:
            # Not valid at this position; continue scanning deeper positions.
            continue
    return None


def strip_code_fences(raw_response: str) -> str:
    """Removes markdown code-fence wrappers commonly emitted by chat models."""
    text = (raw_response or "").strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()
