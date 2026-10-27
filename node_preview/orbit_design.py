"""Approved Orbit presentation for the public desktop and mobile ranking.

Presentation assets are inlined into the existing HTML/runtime objects.  This
module performs no data reads, analytics filtering, publishing or URL rewriting.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

_ROOT = Path(__file__).parent
_ASSETS = _ROOT / "hero_concepts"
_CSS_NAMES = ("shared.css", "orbit.css", "orbit_finish.css", "orbit_finish_hero.css",
              "orbit_sidebar.css", "orbit_report.css")

# The caller includes these in its content version so changing a visual asset
# cannot leave the static HTML cached behind an unchanged builder hash.
ORBIT_RUNTIME_SOURCES = tuple(_ROOT / name for name in (
    "intro_orbit_resonance.js", "orbit_opening.js", "orbit_navigation.js", "orbit_locale.js"))
ORBIT_SOURCES = (Path(__file__), _ASSETS / "orbit.html",
                 *(_ASSETS / name for name in _CSS_NAMES), *ORBIT_RUNTIME_SOURCES)

_SIDEBAR = '''<aside class="mc-sidebar" id="mc-sidebar-menu" aria-label="板块导航"><a class="mc-sidebar-brand" href="#top">MarsChain<span>算力排行榜</span></a><nav><a href="#top">概览</a><a href="#rank">算力排行</a><a href="#pulse">核心数据</a><a href="#growth">周期增长</a><a href="#equation">方程系数</a><a href="#risk">数据说明</a></nav><div class="mc-sidebar-feedback"><span class="mc-feedback-label">意见反馈</span><span class="mc-feedback-email">yunqihaoyun521@163.com</span></div><p>链上数据 · 每分钟更新</p></aside><button class="mc-sidebar-toggle" type="button" aria-expanded="false" aria-controls="mc-sidebar-menu">☰ 板块导航</button>'''
_REPORT_ICON = '''<svg class="mc-report-icon" viewBox="0 0 20 20" fill="none" aria-hidden="true" focusable="false"><path d="M5 2.5h7l3 3v12H5zM12 2.5v4h3M8 10h4M8 13h4" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"/></svg>'''
_TRANSLATIONS = {
    "链上数据 · 每分钟更新": "On-chain data · Minute updates",
    "洞察算力分布，追踪网络增长。": "Track network power and on-chain growth.",
    "算力排行榜": "Power Ranking",
    "查看排行榜": "View ranking",
    "数据说明": "Data notes",
    "数据时间": "Updated",
    "让算力，有迹可循。": "Follow the power behind the network.",
    "概览": "Overview",
    "算力排行": "Power Ranking",
    "核心数据": "Core Metrics",
    "周期增长": "Period Growth",
    "方程系数": "Equation Factors",
    "生成快报": "Create Report",
    "意见反馈": "Feedback",
    "☰ 板块导航": "☰ Navigation",
}

_SUPPLEMENT = '''
html[lang^="en"] .mc-hero--orbit .orbit-title{font-size:clamp(30px,4.4vw,57px);letter-spacing:-.025em}
body.mc-orbit-finish .orbit-eyebrow.is-stale{color:#d0bea9}
@media(max-width:620px){html[lang^="en"] .mc-hero--orbit .orbit-title{font-size:clamp(28px,8.5vw,37px)}html[lang^="en"] .mc-hero--orbit .orbit-description{max-width:260px;font-size:10px}}
'''


def _once(markup: str, old: str, new: str) -> str:
    if markup.count(old) != 1:
        raise ValueError("Orbit design requires one " + old[:55])
    return markup.replace(old, new, 1)


def apply_orbit_design(markup: str, mobile: bool) -> str:
    """Apply the approved hero, palette and collapsible navigation once.

    Call after the ordinary frontend adaptations and before writing the static
    artifact. Runtime sources must be concatenated to the existing deferred live
    runtime instead of the former ``rank_opening.js`` entrypoint.
    """
    if 'id="mc-orbit-design"' in markup:
        raise ValueError("Orbit design already applied")
    hero = (_ASSETS / "orbit.html").read_text(encoding="utf-8")
    hero = re.sub(r'<span class="orbit-art-label">.*?</span>', '', hero, count=1)
    hero = _once(hero, '洞察算力分布，追踪网络增长。<br>以更清晰的视野，看见链上每一步。',
                 '洞察算力分布，追踪网络增长。')
    hero = _once(hero, '数据时间 <span data-hero-data-time>',
                 '<span data-hero-time-label>数据时间</span> <span data-hero-data-time>')
    hero = _once(hero, '<i aria-hidden="true"></i>链上数据 · 每分钟更新',
                 '<i aria-hidden="true"></i><span data-orbit-update-state>链上数据 · 每分钟更新</span>')
    pattern = r'<section\s+class="' + ('m-hero' if mobile else 'hero') + r'"[^>]*>.*?</section>'
    markup, count = re.subn(pattern, lambda _: hero, markup, count=1, flags=re.S)
    if count != 1:
        raise ValueError("Orbit design requires one original hero")

    classes = "mc-orbit-page mc-page-orbit mc-orbit-finish mc-orbit-sidebar mc-report-highlight"
    def body(match):
        attrs = match[1]
        found = re.search(r'\bclass\s*=\s*([\"\'])(.*?)\1', attrs, flags=re.S)
        if found:
            attrs = attrs[:found.start()] + 'class="' + found[2] + ' ' + classes + '"' + attrs[found.end():]
        else:
            attrs += ' class="' + classes + '"'
        if re.search(r'\bid\s*=', attrs):
            raise ValueError("Orbit top anchor requires an unassigned body id")
        return '<body' + attrs + ' id="top">' + _SIDEBAR
    markup, count = re.subn(r'<body\b([^>]*)>', body, markup, count=1)
    if count != 1:
        raise ValueError("Orbit design requires one body")

    if mobile:
        labels = {"排行": "算力排行", "核心": "核心数据", "周期": "周期增长", "方程": "方程系数", "说明": "数据说明"}
        def full_navigation(match):
            links = re.sub(r'(<a\b[^>]*>)(排行|核心|周期|方程|说明)(</a>)',
                           lambda link: link[1] + labels[link[2]] + link[3], match[2])
            return match[1] + links + match[3]
        markup, count = re.subn(r'(<nav class="m-nav"[^>]*>)(.*?)(</nav>)',
                               full_navigation, markup, flags=re.S)
        if count != 1:
            raise ValueError("Orbit design requires one mobile navigation")

    markup, count = re.subn(r'(<button\b[^>]*\bdata-share-poster\b[^>]*>)(?:快报|生成快报)(</button>)',
                           lambda button: button[1] + _REPORT_ICON + '<span>生成快报</span>' + button[2], markup)
    if not count:
        raise ValueError("Orbit design requires the existing report action")
    translations = "\n" + "\n".join(json.dumps(zh, ensure_ascii=False) + ": " + json.dumps(en) + ","
                                     for zh, en in _TRANSLATIONS.items())
    markup = _once(markup, '  const zhToEn = {', '  const zhToEn = {' + translations)
    css = "\n".join((_ASSETS / name).read_text(encoding="utf-8") for name in _CSS_NAMES)
    # Reuse the approved source treatments without exposing a preview class or
    # requiring extra uploaded assets in the formal object allowlist.
    css = css.replace("mc-concept-page", "mc-orbit-page")
    return _once(markup, '</head>', '<style id="mc-orbit-design">\n' + css + _SUPPLEMENT + '\n</style>\n</head>')
