"""
Long-term (cross-session) memory for the Cafe Intelligence Agent.

Requirement from the assignment: "remembers previous weeks, so it can say
'this is the third week in a row' and 'you approved this idea last month
and it didn't run.' Cross-session, not just cross-turn."

Design choice: a plain JSON file on disk. The scheduler runs this as a
fresh Python process each week, so memory needs to survive a full
restart -- an in-memory store would lose everything between runs. A JSON
file is simple, transparent, genuinely persists across restarts, and
needs no extra dependency or database setup.
"""
import json
import os
from datetime import datetime, timedelta


DEFAULT_STORE_PATH = "memory_store.json"


class WeeklyMemoryStore:
    """Reads/writes a single JSON file holding all past weeks' data.
    One instance per run; each write re-saves the whole file (the data
    volume here -- summaries, not raw transactions -- stays small even
    after a year of weekly runs)."""

    def __init__(self, path: str = DEFAULT_STORE_PATH):
        self.path = path
        self._data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        return {"weeks": {}, "content_ideas": []}

    def _save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    # --- Weekly summaries -------------------------------------------------
    def save_week(self, week_id: str, summary: dict) -> None:
        """summary should be small and serializable: e.g.
        {"top_findings": [...], "critic_rejection_count": 2,
         "verified_finding_count": 159, "run_timestamp": "..."}"""
        self._data["weeks"][week_id] = {
            **summary,
            "saved_at": datetime.now().isoformat(),
        }
        self._save()

    def get_week(self, week_id: str) -> dict | None:
        return self._data["weeks"].get(week_id)

    def get_recent_weeks(self, n: int = 4) -> list[tuple[str, dict]]:
        """Returns up to n most recent weeks, oldest first, by week_id
        string sort (works correctly for "YYYY-Www" formatted IDs)."""
        items = sorted(self._data["weeks"].items())
        return items[-n:]

    # --- Streak detection ("third week in a row...") ----------------------
    def find_streak(self, claim_keyword: str, direction: str = "spike", max_weeks: int = 8) -> int:
        """
        Counts how many of the most recent consecutive weeks had a
        verified finding whose claim contains claim_keyword and direction
        (e.g. claim_keyword="coffee", direction="spike" -> checks recent
        weeks for a claim like "Coffee sales spike..."). Returns 0 if the
        most recent week doesn't match (streak must be unbroken and current).
        """
        recent = self.get_recent_weeks(max_weeks)
        streak = 0
        for week_id, summary in reversed(recent):
            findings = summary.get("top_findings", [])
            matched = any(
                claim_keyword.lower() in f.get("claim", "").lower()
                and direction.lower() in f.get("claim", "").lower()
                for f in findings
            )
            if matched:
                streak += 1
            else:
                break
        return streak

    # --- Content idea tracking ("you approved this last month...") --------
    def save_content_idea(self, week_id: str, idea: dict, approved: bool) -> None:
        """idea should include at least a short 'hook' or 'id' field so it
        can be matched again later."""
        self._data["content_ideas"].append({
            "week_id": week_id,
            "idea": idea,
            "approved": approved,
            "saved_at": datetime.now().isoformat(),
        })
        self._save()

    def find_matching_past_idea(self, idea_hook: str, weeks_back: int = 8, overlap_threshold: float = 0.4) -> dict | None:
        """
        Checks if a similar content idea (by hook text) was proposed and
        approved in a recent past week -- used to flag "you approved this
        idea last month and it didn't run" style findings for the content
        agent. Uses word-overlap similarity (not exact substring), since
        two ideas about the same thing rarely share identical phrasing
        (e.g. "Cold Brew is back for summer" vs "Try our new Cold Brew
        this summer" -- clearly the same idea, no shared substring).
        """
        STOPWORDS = {"the", "a", "an", "is", "are", "this", "our", "new", "for", "to", "of", "in", "on"}

        def keywords(text: str) -> set[str]:
            return {w for w in text.lower().split() if len(w) > 2 and w not in STOPWORDS}

        idea_words = keywords(idea_hook)
        if not idea_words:
            return None

        cutoff = datetime.now() - timedelta(weeks=weeks_back)
        for entry in reversed(self._data["content_ideas"]):
            saved_at = datetime.fromisoformat(entry["saved_at"])
            if saved_at < cutoff:
                break
            past_words = keywords(entry["idea"].get("hook", ""))
            if not past_words:
                continue
            overlap = len(idea_words & past_words) / len(idea_words | past_words)
            if overlap >= overlap_threshold:
                return entry
        return None


def build_week_summary(verified_findings: list[dict], rejection_log: list[str]) -> dict:
    """
    Helper for graph.py: converts a run's verified_findings + rejection_log
    into the small summary dict that actually gets stored (not the full
    findings list forever -- just what's needed for streak detection and
    the report's "3rd week in a row" style callouts).
    """
    return {
        "top_findings": [
            {"agent": f["agent"], "claim": f["claim"], "number": f["number"]}
            for f in verified_findings
        ],
        "verified_finding_count": len(verified_findings),
        "critic_rejection_count": len(rejection_log),
    }


if __name__ == "__main__":
    # Standalone demo: simulate 3 weeks where "Cold Brew" kept spiking,
    # to prove the streak detector actually works end-to-end.
    import tempfile

    demo_path = os.path.join(tempfile.gettempdir(), "memory_store_demo.json")
    if os.path.exists(demo_path):
        os.remove(demo_path)

    store = WeeklyMemoryStore(demo_path)

    store.save_week("2026-W25", build_week_summary(
        [{"agent": "anomaly", "claim": "Cold Brew sales spike on 2026-06-20", "number": 2.5}], []
    ))
    store.save_week("2026-W26", build_week_summary(
        [{"agent": "anomaly", "claim": "Cold Brew sales spike on 2026-06-27", "number": 2.3}], []
    ))
    store.save_week("2026-W27", build_week_summary(
        [{"agent": "anomaly", "claim": "Cold Brew sales spike on 2026-07-03", "number": 2.4}], []
    ))

    streak = store.find_streak("cold brew", "spike")
    print(f"Cold Brew spike streak: {streak} consecutive weeks")

    store.save_content_idea("2026-W25", {"hook": "Try our new Cold Brew this summer"}, approved=True)
    match = store.find_matching_past_idea("Cold Brew is back for summer")
    print(f"Matching past idea found: {match is not None}")
    if match:
        print(f"  from week {match['week_id']}, approved={match['approved']}")

    os.remove(demo_path)