"""S48c2 (#220 4.12): the briefing texts equal canon's fixed script (05-story-arcs).

The anchor line 3.3 is never dropped, so it must never say the opposite of
the rules. The texts are staged as script lines and read in full through
Vantongeren's "Hear it again"; both come from these files.
"""

import re
from pathlib import Path


def screens(doc: str) -> list[str]:
    section = doc.split("### The Wake briefing", 1)[1].split("\n### ", 1)[0]
    blocks = re.split(r"\*\*Screen \d\*\*", section)[1:]
    out = []
    for block in blocks:
        quoted = [line[1:].strip() for line in block.splitlines() if line.startswith(">")]
        out.append(" ".join(" ".join(quoted).split()))
    return out


def test_the_briefing_texts_are_canons_fixed_script(repo_root: Path) -> None:
    doc = (repo_root/"docs/world/05-story-arcs.md").read_text(encoding="utf-8")
    canon = screens(doc)
    assert len(canon) == 3
    for index, expected in enumerate(canon, 1):
        text = (repo_root/f"src/karst41/content/text/wake-briefing-{index}.md").read_text(encoding="utf-8")
        assert " ".join(text.split()) == expected, f"wake-briefing-{index}.md differs from 05-story-arcs.md"
