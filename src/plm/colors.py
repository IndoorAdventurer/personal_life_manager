"""Project colour palette, shared by the web UI and the ICS feed."""

# Predefined palette — 10 perceptually distinct colours chosen to look good on
# both white card backgrounds and dark nav.  Index is determined by hashing the
# project UUID so the same project always gets the same colour.
#
# Each entry pairs the hex used by the web UI with the nearest CSS3 colour name,
# because the ICS COLOR property (RFC 7986) only accepts CSS3 names, not hex.
PALETTE: list[tuple[str, str]] = [
    ("#3b82f6", "royalblue"),        # blue
    ("#10b981", "mediumseagreen"),   # emerald
    ("#f59e0b", "goldenrod"),        # amber
    ("#ef4444", "tomato"),           # red
    ("#8b5cf6", "mediumpurple"),     # violet
    ("#06b6d4", "darkturquoise"),    # cyan
    ("#f97316", "darkorange"),       # orange
    ("#84cc16", "yellowgreen"),      # lime
    ("#ec4899", "hotpink"),          # pink
    ("#6366f1", "slateblue"),        # indigo
]


def palette_index(key: str) -> int:
    """Deterministic palette index for *key* (a project id or tag).

    sum(bytes) mod palette_length — stable across restarts, no external dep.
    UUIDs have enough byte variation that adjacent IDs rarely get the same colour.
    """
    return sum(key.encode()) % len(PALETTE)


def project_color(project_id: str) -> str:
    """Hex colour for a project (web UI)."""
    return PALETTE[palette_index(project_id)][0]


def project_css_color(project_id: str) -> str:
    """CSS3 colour name for a project (ICS COLOR property)."""
    return PALETTE[palette_index(project_id)][1]
