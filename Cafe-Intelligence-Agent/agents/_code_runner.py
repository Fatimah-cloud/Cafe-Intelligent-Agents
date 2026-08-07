"""
Shared subprocess-execution helper with REAL self-correction: on failure,
the error is sent back to an LLM that rewrites the code, and the fixed
version is retried — not just re-running the same code blindly.

Used by every analyst agent so this logic exists in one place.
"""
import subprocess
import sys
import json
from dotenv import load_dotenv

load_dotenv()

_llm = None


def _get_llm():
    """Lazy init — avoids requiring langchain_google_genai to even be
    installed unless the fix-up path is actually reached."""
    global _llm
    if _llm is None:
        from langchain_google_genai import ChatGoogleGenerativeAI
        _llm = ChatGoogleGenerativeAI(model="gemini-3.1-flash-lite")
    return _llm


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return str(content)


def _run_once(code: str, timeout: int) -> dict:
    """Runs code in a fresh subprocess. Never exec()'d in this process."""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode != 0:
            return {"ok": False, "error": proc.stderr.strip()[-2000:]}
        return {"ok": True, "data": json.loads(proc.stdout.strip())}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Execution timed out"}
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"Output was not valid JSON: {e}"}


def _ask_llm_to_fix(code: str, error: str) -> str:
    """Sends the failing code + its exact error to the model and asks for
    a corrected version. This is the real self-correction step."""
    prompt = (
        "This Python script failed. Fix it and return ONLY the corrected "
        "Python code, no explanation, no markdown fences.\n\n"
        f"--- Code ---\n{code}\n\n"
        f"--- Error ---\n{error}\n"
    )
    response = _get_llm().invoke(prompt)
    fixed = _extract_text(response.content).strip()
    # strip accidental markdown fences if the model adds them anyway
    if fixed.startswith("```"):
        fixed = fixed.split("```")[1]
        if fixed.startswith("python"):
            fixed = fixed[len("python"):]
    return fixed.strip()


def run_self_correcting_code(code: str, timeout: int = 15, max_fix_attempts: int = 3) -> dict:
    """
    Runs `code` in a subprocess. On failure, sends the exact error back to
    the model to produce a fixed version, and retries — up to
    max_fix_attempts times. Returns the last attempt's result either way,
    plus a log of what happened at each attempt (useful for the report's
    "self-correction loop" evidence).
    """
    attempts_log = []
    current_code = code

    for attempt in range(max_fix_attempts + 1):
        result = _run_once(current_code, timeout)
        attempts_log.append({"attempt": attempt, "ok": result["ok"], "error": result.get("error")})

        if result["ok"]:
            result["attempts_log"] = attempts_log
            return result

        if attempt < max_fix_attempts:
            current_code = _ask_llm_to_fix(current_code, result["error"])

    result["attempts_log"] = attempts_log
    return result