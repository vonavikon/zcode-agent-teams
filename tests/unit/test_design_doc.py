"""`DESIGN.md` records the visual system of the board.

The stylesheet is the source of truth; the record follows it. These
tests keep the two together: a colour changed in the stylesheet without
the record (or the other way round) fails here, and so does a document
that loses the eight canonical sections a DESIGN.md-aware tool reads.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "DESIGN.md"
CSS = ROOT / "plugins/agent-teams/skills/agent-teams/scripts/agent_teams/dashboard/app.css"
SECTIONS = ["Overview", "Colors", "Typography", "Layout", "Elevation & Depth", "Shapes",
            "Components", "Do's and Don'ts"]
TOP_LEVEL = {"name", "description", "colors", "typography", "rounded", "spacing", "components"}
SCALARS = {"name", "description"}
PROPS = {"backgroundColor", "textColor", "typography", "rounded", "padding", "size", "height",
         "width"}


def frontmatter() -> dict:
    """The frontmatter as nested dicts, read by indentation only (the
    standard library has no YAML parser): a group opens with a line that
    has no value, an entry sits two spaces in, a property of an entry four."""
    text = DOC.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "the document opens with YAML frontmatter"
    block = text[4:text.index("\n---\n", 4)]
    root: dict = {}
    stack: list[tuple[int, dict]] = [(-1, root)]
    for raw in block.split("\n"):
        line = raw.split("  #", 1)[0].rstrip()          # a comment after a value
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        key, _, value = line.strip().partition(":")
        while indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value.strip():
            parent[key] = value.strip().strip('"')
        else:
            parent[key] = {}
            stack.append((indent, parent[key]))
    return root


def normal(colour: str) -> str:
    return re.sub(r"\s+", "", colour).upper()


def css_colours() -> set[str]:
    """Every value of a custom property of the stylesheet that is a colour.
    A line may declare more than one property (the dark theme does)."""
    declared = re.findall(r"(--[a-z0-9-]+):\s*([^;{}]+);", CSS.read_text(encoding="utf-8"))
    return {normal(value) for _name, value in declared
            if re.match(r"#[0-9A-Fa-f]{6}$|rgba?\(", value.strip())}


def test_the_document_keeps_the_eight_sections_in_their_order():
    heads = re.findall(r"^## (.+)$", DOC.read_text(encoding="utf-8"), re.M)
    assert heads == SECTIONS


def test_the_frontmatter_speaks_the_schema_of_the_format():
    root = frontmatter()
    assert set(root) <= TOP_LEVEL and SCALARS <= set(root)
    for name, group in root.items():
        if name not in SCALARS:
            assert isinstance(group, dict) and group, name
    for name, component in root.get("components", {}).items():
        for prop, value in component.items():
            assert prop in PROPS, (name, prop)
            for group, key in re.findall(r"\{([^}]+)\.([^}]+)\}", value):
                assert key in root.get(group, {}), (name, group, key)


def test_every_colour_of_the_record_is_a_token_of_the_stylesheet():
    declared = css_colours()
    for key, value in frontmatter()["colors"].items():
        assert normal(value) in declared, f"{key}: {value} is not in the stylesheet"


def test_every_colour_of_the_stylesheet_is_in_the_record():
    """A new colour token must be recorded, not left in the CSS alone."""
    recorded = {normal(value) for value in frontmatter()["colors"].values()}
    assert css_colours() - recorded == set()


def test_the_record_carries_no_long_dash():
    """The owner does not want them, in any text written for them."""
    text = DOC.read_text(encoding="utf-8")
    assert "\u2014" not in text and "\u2013" not in text
