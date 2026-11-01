"""Package the approved report into the existing content-addressed runtime."""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARTWORK = ROOT / "assets/mars-masthead-20261029.png"
REPORT_SOURCES = (
    Path(__file__), ROOT / "share_report.py", ROOT / "share_report_helpers.js",
    ROOT / "report_daily_live_data.js", ROOT / "report_unified_canvas.js",
    ROOT / "report_english_canvas.js",
    ROOT / "share_report_ui.js", ARTWORK,
)


def report_runtime() -> str:
    artwork = "data:image/png;base64," + base64.b64encode(ARTWORK.read_bytes()).decode("ascii")
    renderers = []
    for name in ("report_unified_canvas.js", "report_english_canvas.js"):
        renderer = (ROOT / name).read_text(encoding="utf-8")
        renderer, count = re.subn(
            r"image\.src='assets/mars-masthead-20261029\.png\?v=[a-f0-9]+';",
            "image.src=marsReportArtworkUri;", renderer,
        )
        if count != 1:
            raise RuntimeError("Approved report artwork marker changed: " + name)
        renderers.append(renderer)
    # Both languages share the original image bytes. Duplicating its base64
    # would enlarge the public runtime and risk its existing upload size limit.
    parts = ["(() => { const marsReportArtworkUri=" + json.dumps(artwork) + ";"]
    parts.extend((ROOT / name).read_text(encoding="utf-8") for name in (
        "share_report_helpers.js", "report_daily_live_data.js"))
    parts.extend((*renderers, (ROOT / "share_report_ui.js").read_text(encoding="utf-8"), "})();"))
    return "\n".join(parts)
