#!/usr/bin/env python3
"""Fetch Chinese web hotspots and write a daily markdown report.

This script is intentionally dependency-light. It uses DailyHotApi-compatible
JSON endpoints by default, then normalizes records into one report.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_STOPWORDS = {
    "一个", "什么", "为什么", "如何", "怎么", "可以", "还是", "不是", "没有",
    "中国", "网友", "回应", "官方", "最新", "发布", "宣布", "今日", "视频",
    "the", "and", "with", "for", "from", "this", "that",
}


def load_simple_yaml(path: Path) -> dict[str, Any]:
    """Load the small YAML subset used by sources.yaml without dependencies."""
    text = path.read_text(encoding="utf-8")
    config: dict[str, Any] = {"sources": []}
    current: dict[str, Any] | None = None

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        if line == "sources:":
            continue
        if line.startswith("- "):
            current = {}
            config["sources"].append(current)
            key, value = parse_key_value(line[2:])
            current[key] = value
            continue
        key, value = parse_key_value(line)
        if indent >= 2 and current is not None:
            current[key] = value
        else:
            config[key] = value
    return config


def parse_key_value(line: str) -> tuple[str, Any]:
    key, value = line.split(":", 1)
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    elif re.fullmatch(r"-?\d+", value):
        value = int(value)
    return key.strip(), value


def fetch_json(url: str, timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "hotspot-report/1.0 (+daily report workflow)",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
    return json.loads(payload.decode("utf-8", errors="replace"))


def normalize_items(payload: dict[str, Any], source: dict[str, Any], top_n: int) -> list[dict[str, Any]]:
    data = payload.get("data", payload.get("list", []))
    if isinstance(data, dict):
        data = data.get("list") or data.get("items") or data.get("data") or []
    if not isinstance(data, list):
        data = []

    normalized: list[dict[str, Any]] = []
    for rank, item in enumerate(data[:top_n], start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or item.get("word") or "").strip()
        if not title:
            continue
        normalized.append(
            {
                "source_id": source["id"],
                "source_name": source.get("name", source["id"]),
                "category": source.get("category", "未分类"),
                "rank": item.get("rank") or item.get("index") or rank,
                "title": title,
                "hot": item.get("hot") or item.get("hot_value") or item.get("views") or item.get("score") or item.get("heat") or "",
                "url": item.get("url") or item.get("link") or item.get("mobileUrl") or "",
                "desc": item.get("desc") or item.get("description") or "",
            }
        )
    return normalized


def title_key(title: str) -> str:
    title = re.sub(r"\s+", "", title.lower())
    title = re.sub(r"[^\w\u4e00-\u9fff]", "", title)
    return title[:60]


def tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_+-]{2,}|[\u4e00-\u9fff]{2,6}", text)
    return [token for token in tokens if token.lower() not in DEFAULT_STOPWORDS]


def build_report(items: list[dict[str, Any]], errors: list[dict[str, str]], generated_at: str) -> str:
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_source[item["source_name"]].append(item)

    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        key = title_key(item["title"])
        if key not in unique:
            unique[key] = {**item, "sources": [item["source_name"]]}
        else:
            unique[key]["sources"].append(item["source_name"])

    keyword_counter = Counter()
    for item in items:
        keyword_counter.update(tokenize(item["title"]))

    lines: list[str] = []
    report_date = generated_at.split("T", 1)[0]
    lines.append(f"# 中国网站热点日报 - {report_date}")
    lines.append("")
    lines.append(f"生成时间：{generated_at}")
    lines.append("")
    lines.append("## 摘要")
    lines.append("")
    lines.append(f"- 抓取源数量：{len(by_source)}")
    lines.append(f"- 原始热点条数：{len(items)}")
    lines.append(f"- 去重后热点条数：{len(unique)}")
    if keyword_counter:
        top_keywords = "、".join(word for word, _ in keyword_counter.most_common(12))
        lines.append(f"- 高频关键词：{top_keywords}")
    if errors:
        lines.append(f"- 异常源数量：{len(errors)}")
    lines.append("")

    lines.append("## 跨站重复/共振")
    lines.append("")
    repeated = [item for item in unique.values() if len(set(item["sources"])) > 1]
    if not repeated:
        lines.append("- 暂无明显跨站重复热点。")
    else:
        repeated.sort(key=lambda item: len(set(item["sources"])), reverse=True)
        for item in repeated[:20]:
            sources = "、".join(sorted(set(item["sources"])))
            lines.append(f"- {item['title']}（{sources}）")
    lines.append("")

    lines.append("## 分站热点")
    lines.append("")
    for source_name, source_items in by_source.items():
        lines.append(f"### {source_name}")
        lines.append("")
        for item in source_items:
            hot = f" | 热度：{item['hot']}" if item.get("hot") else ""
            url = item.get("url") or ""
            if url:
                lines.append(f"{item['rank']}. [{item['title']}]({url}){hot}")
            else:
                lines.append(f"{item['rank']}. {item['title']}{hot}")
        lines.append("")

    if errors:
        lines.append("## 抓取异常")
        lines.append("")
        for error in errors:
            lines.append(f"- {error['source']}: {error['error']}")
        lines.append("")

    lines.append("## 下一步建议")
    lines.append("")
    lines.append("- 把稳定源保留，把经常失败的源切换成自建接口或 RSS。")
    lines.append("- 如果要给老板/客户看，建议增加 LLM 摘要层：按行业、风险、机会、舆情信号重写。")
    lines.append("- 如果要做长期观察，建议把 raw JSON 入库，再做趋势图和关键词变化。")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a daily Chinese hotspot report.")
    parser.add_argument("--config", default="sources.yaml", help="Path to sources.yaml")
    parser.add_argument("--out-dir", default="reports", help="Directory for markdown reports")
    parser.add_argument("--data-dir", default="data", help="Directory for raw JSON snapshots")
    parser.add_argument("--base-url", default=None, help="Override base URL; used as {base_url}/{source_id}")
    parser.add_argument("--url-template", default=None, help="Override request URL template; use {id} for source id")
    parser.add_argument("--top-n", type=int, default=None, help="Override top N per source")
    args = parser.parse_args()

    config_path = Path(args.config)
    config = load_simple_yaml(config_path)
    url_template = args.url_template or config.get("url_template")
    base_url = (args.base_url or config.get("base_url") or "").rstrip("/")
    top_n = int(args.top_n or config.get("top_n_per_source") or 20)
    timeout = int(config.get("timeout_seconds") or 20)

    if not url_template and not base_url:
        print("Missing url_template/base_url. Set it in sources.yaml or pass --url-template.", file=sys.stderr)
        return 2

    generated_at = dt.datetime.now().astimezone().replace(microsecond=0).isoformat()
    stamp = generated_at[:10]
    all_items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for source in config.get("sources", []):
        source_id = source["id"]
        url = url_template.format(id=source_id) if url_template else f"{base_url}/{source_id}"
        try:
            payload = fetch_json(url, timeout)
            all_items.extend(normalize_items(payload, source, top_n))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            errors.append({"source": source.get("name", source_id), "error": str(exc)})

    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    snapshot = {
        "generated_at": generated_at,
        "url_template": url_template,
        "base_url": base_url,
        "items": all_items,
        "errors": errors,
    }
    (data_dir / f"{stamp}.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report = build_report(all_items, errors, generated_at)
    report_path = out_dir / f"{stamp}.md"
    report_path.write_text(report, encoding="utf-8")
    print(report_path)
    if errors and not all_items:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
