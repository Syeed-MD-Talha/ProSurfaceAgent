"""One-command knowledge-base rebuild: crawl -> verify.

Run from the repo root:
    ..\\.venv\\Scripts\\python.exe scripts\\rebuild_kb.py

Regenerates prosurfaceworks_knowledge_base/pages/ from the live site,
then verifies skills + retrieval.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_step(label: str, script: str) -> None:
    print(f"\n=== {label}: {script} ===")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    print(proc.stdout[-2000:])
    if proc.returncode != 0:
        print(proc.stderr[-2000:])
        raise SystemExit(f"{script} failed (exit {proc.returncode})")


def verify() -> None:
    sys.path.insert(0, str(ROOT))
    from prosurface_agent.data import DataStore
    from prosurface_agent.skills.store import SkillStore

    skills = SkillStore(project_dir=ROOT).list_skills()
    print(f"\nskills: {len(skills)}")
    for entry in skills:
        print(f"  - {entry.name} [{entry.category}]")

    store = DataStore(project_dir=ROOT)
    info = store.reload()
    print(f"retrieval: {info['chunks']} chunks across {len(info['files'])} files")
    probe = store.search("parquet polishing cost", top_k=3)
    print(f"probe 'parquet polishing cost' -> {len(probe.get('results', []))} hits")
    if not skills or not info["chunks"] or not probe.get("results"):
        raise SystemExit("verify FAILED: empty skills, chunks, or probe results")
    print("\nREBUILD OK")


def main() -> None:
    if "--verify-only" in sys.argv:
        verify()
        return
    run_step("crawl", "crawl_website.py")
    verify()


if __name__ == "__main__":
    main()
