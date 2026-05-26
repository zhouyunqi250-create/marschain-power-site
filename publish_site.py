#!/usr/bin/env python3
"""Publish the latest MarsChain dashboard as a deployable static site."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from build_frontend_dashboard import build_html

PUBLIC_RANK_LIMIT = 100


README_TEXT = """# MarsChain Power Dashboard

这是一个可直接部署的静态站目录。

## 本地预览

```bash
cd site
python3 -m http.server 8000
```

然后打开：

- [http://127.0.0.1:8000](http://127.0.0.1:8000)

## 部署方式

### 1. GitHub Pages

把 `site/` 目录内容推到一个仓库的默认分支根目录，或者 `docs/` 目录，然后在 GitHub Pages 里启用静态托管。

### 2. Netlify Drop

打开 [https://app.netlify.com/drop](https://app.netlify.com/drop)，把整个 `site/` 目录拖进去。

### 3. Cloudflare Pages

新建一个 Pages 项目，把 `site/` 当成输出目录即可。
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a deployable static site from a ranking JSON file.")
    parser.add_argument("input", help="Ranking JSON path.")
    parser.add_argument("--site-dir", default="site", help="Output site directory.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = Path(args.input)
    site_dir = Path(args.site_dir)
    data_dir = site_dir / "data"
    payload = json.loads(input_path.read_text())

    site_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    downloads_dir = site_dir / "downloads"
    if downloads_dir.exists():
        shutil.rmtree(downloads_dir)

    public_payload = {
        "meta": {
            **payload.get("meta", {}),
            "full_ranked_count": len(payload.get("rows", [])),
            "public_rank_limit": PUBLIC_RANK_LIMIT,
            "ranked_count": min(PUBLIC_RANK_LIMIT, len(payload.get("rows", []))),
        },
        "rows": payload.get("rows", [])[:PUBLIC_RANK_LIMIT],
    }

    (site_dir / "index.html").write_text(build_html(public_payload))
    (site_dir / "data" / "latest.json").write_text(json.dumps(public_payload, ensure_ascii=False, indent=2) + "\n")
    (site_dir / "robots.txt").write_text("User-agent: *\nAllow: /\n")
    (site_dir / "README.md").write_text(README_TEXT)

    print(site_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
