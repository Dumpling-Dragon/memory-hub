from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .web_export_common import (
    default_gemini_json_dir,
    default_gemini_owner_path,
    default_yuanbao_json_dir,
    default_yuanbao_owner_path,
    workspace_root,
)


CLASSMATE_FINANCE = [
    "投行",
    "fa",
    "实习",
    "简历",
    "邮件",
    "金融",
    "估值",
    "dcf",
    "lbo",
    "ipo",
    "并购",
    "研报",
    "券商",
    "资本",
    "财务",
    "cfa",
    "pe",
    "vc",
    "行研",
    "港股",
    "美股",
    "股票",
    "证券",
    "基金",
    "创投",
    "投资",
    "投研",
    "资产管理",
    "招股",
    "市值",
    "市场规模",
    "公共财政",
    "财政支出",
    "经济法",
    "咨询面试",
    "行业研究",
    "行业分析",
    "cpo",
    "硅光",
    "hbm",
    "不正当竞争法",
]
CLASSMATE_PHOTO = ["摄影", "相机", "镜头", "光圈", "焦距", "拍摄", "富士", "索尼", "佳能", "尼康", "lightroom", "胶片"]
CLASSMATE_FORTUNE = ["命理", "算命", "占卜", "塔罗", "紫微", "命盘", "八字", "星盘", "面相", "运势", "玄学"]
CLASSMATE_SOCIAL = [
    "社交",
    "社恐",
    "人际交往",
    "暧昧",
    "恋爱",
    "亲密关系",
    "感情关系",
    "好感信号",
    "已读不回",
    "聊天技巧",
    "关系降级",
    "networking",
    "network",
    "导师回馈",
    "亲信",
]
ME_TECH = [
    "ctf",
    "网安",
    "渗透",
    "漏洞",
    "cve",
    "pwn",
    "逆向",
    "re",
    "ida",
    "ghidra",
    "frida",
    "android",
    "apk",
    "汇编",
    "docker",
    "python",
    "java",
    "linux",
    "kali",
    "sql注入",
    "xss",
    "ssti",
    "取证",
    "服务器",
    "mcp",
    "codex",
    "claude",
    "hermes",
    "浏览器",
    "脚本",
    "api",
    "数据结构",
    "esp32",
]
ME_HEALTH = [
    "adhd",
    "注意力",
    "贯注",
    "专注达",
    "右佐匹克隆",
    "抗抑郁",
    "睡眠",
    "焦虑",
    "医院",
    "身体",
    "用药",
    "普瑞巴林",
    "咽喉炎",
]
ME_AI = ["ai", "agent", "llm", "大模型", "rag", "embedding", "reranker", "向量", "gemini", "元宝", "助手"]
UNCERTAIN = ["心理学", "行为经济学", "咨询", "情绪", "人格", "关系"]


def parse_args() -> argparse.Namespace:
    root = workspace_root() / "按对话归类"
    default_yuanbao_owner = default_yuanbao_owner_path()
    parser = argparse.ArgumentParser(description="Classify web AI conversations by likely owner/topic.")
    parser.add_argument("--gemini-json-dir", type=Path, default=default_gemini_json_dir())
    parser.add_argument("--yuanbao-json-dir", type=Path, default=default_yuanbao_json_dir())
    parser.add_argument("--gemini-output", type=Path, default=default_gemini_owner_path())
    parser.add_argument("--yuanbao-output", type=Path, default=default_yuanbao_owner)
    parser.add_argument("--report", type=Path, default=Path("logs/web-classification-report.json"))
    parser.add_argument("--platform", choices=("both", "gemini", "yuanbao"), default="both")
    return parser.parse_args()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def safe_markdown_name(json_file: str) -> str:
    return re.sub(r"\.json$", ".md", json_file, flags=re.I)


def messages_preview(record: dict[str, Any], limit: int = 2400) -> str:
    pieces = [str(record.get("title") or "")]
    for message in record.get("messages") or []:
        text = str(message.get("text") or message.get("content") or "")
        if text.strip():
            pieces.append(text.strip())
        if sum(len(piece) for piece in pieces) > limit:
            break
    return "\n".join(pieces)[:limit].lower()


def contains_any(text: str, keywords: list[str]) -> list[str]:
    hits = []
    for keyword in keywords:
        needle = keyword.lower()
        if re.fullmatch(r"[a-z0-9+#._-]+", needle):
            if re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", text):
                hits.append(keyword)
        elif needle in text:
            hits.append(keyword)
    return hits


def classify_record(record: dict[str, Any]) -> tuple[str, str, str, str]:
    text = messages_preview(record)
    finance = contains_any(text, CLASSMATE_FINANCE)
    photo = contains_any(text, CLASSMATE_PHOTO)
    fortune = contains_any(text, CLASSMATE_FORTUNE)
    social = contains_any(text, CLASSMATE_SOCIAL)
    tech = contains_any(text, ME_TECH)
    health = contains_any(text, ME_HEALTH)
    ai = contains_any(text, ME_AI)
    uncertain = contains_any(text, UNCERTAIN)

    if fortune:
        return "同学", "high", "fortune", f"命理/占卜规则命中: {', '.join(fortune[:5])}"
    if finance:
        return "同学", "high", "finance", f"金融/求职规则命中: {', '.join(finance[:5])}"
    if photo:
        return "同学", "high", "photography", f"摄影规则命中: {', '.join(photo[:5])}"
    if social:
        return "同学", "high", "social", f"社交/关系规则命中: {', '.join(social[:5])}"
    if health:
        return "我", "high", "adhd-health", f"ADHD/身体健康规则命中: {', '.join(health[:5])}"
    if tech:
        return "我", "high", "cyber-tech", f"网安/技术规则命中: {', '.join(tech[:5])}"
    if ai:
        return "我", "medium", "ai-tools", f"AI/Agent 规则命中: {', '.join(ai[:5])}"
    if uncertain:
        return "待确认", "low", "uncertain-psychology", f"心理学/关系类需确认: {', '.join(uncertain[:5])}"
    return "待确认", "low", "uncertain", "标题/规则不足以稳定判断"


def load_confirmed(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    rows = read_json(path)
    confirmed = {}
    for row in rows if isinstance(rows, list) else []:
        json_file = row.get("JsonFile") or row.get("jsonFile")
        confidence = str(row.get("Confidence") or "").lower()
        if json_file and ("user-confirmed" in confidence or "manual" in confidence):
            confirmed[json_file] = row
    return confirmed


def classify_dir(json_dir: Path, output: Path, platform: str) -> dict[str, Any]:
    confirmed = load_confirmed(output)
    rows: list[dict[str, Any]] = []
    if not json_dir.exists():
        raise FileNotFoundError(f"Export directory unavailable; classification preserved: {json_dir}")

    for index, file in enumerate(sorted(json_dir.glob("*.json")), start=1):
        record = read_json(file)
        title = str(record.get("title") or file.stem)
        existing = confirmed.get(file.name)
        if existing:
            row = {
                "Index": str(existing.get("Index") or f"{index:05d}"),
                "Owner": existing.get("Owner") or "待确认",
                "Confidence": existing.get("Confidence") or "user-confirmed",
                "Category": existing.get("Category") or "user-confirmed",
                "Title": title,
                "JsonFile": file.name,
                "MarkdownFile": existing.get("MarkdownFile") or safe_markdown_name(file.name),
                "Reason": existing.get("Reason") or "保留用户确认归属",
            }
        else:
            owner, confidence, category, reason = classify_record(record)
            row = {
                "Index": f"{index:05d}",
                "Owner": owner,
                "Confidence": confidence,
                "Category": category,
                "Title": title,
                "JsonFile": file.name,
                "MarkdownFile": safe_markdown_name(file.name),
                "Reason": reason,
            }
        rows.append(row)

    write_json(output, rows)
    write_csv(output.with_suffix(".csv"), rows)
    uncertain = [row for row in rows if row["Owner"] == "待确认"]
    counts = Counter(row["Owner"] for row in rows)
    category_counts = Counter(row["Category"] for row in rows)
    return {
        "platform": platform,
        "records": len(rows),
        "counts": dict(counts),
        "category_counts": dict(category_counts),
        "output": str(output),
        "uncertain_count": len(uncertain),
        "uncertain": uncertain[:80],
    }


def main() -> None:
    args = parse_args()
    report = read_json(args.report) if args.report.exists() else {}
    if args.platform in {"both", "gemini"}:
        report["gemini"] = classify_dir(args.gemini_json_dir, args.gemini_output, "gemini")
    if args.platform in {"both", "yuanbao"}:
        report["yuanbao"] = classify_dir(args.yuanbao_json_dir, args.yuanbao_output, "yuanbao")
    write_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
