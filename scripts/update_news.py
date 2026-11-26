#!/usr/bin/env python3
"""
午间万象 · 每日自动更新脚本（通用任意大模型 API Key 版，纯 Python 3 标准库，零第三方依赖）

支持的任意 API Key 配置方式（在 GitHub 仓库 Settings -> Secrets -> Actions 中任选其一）：
1. 通用 OpenAI 兼容接口（推荐，通吃 DeepSeek / Kimi / 通义千问 / 智谱 GLM / 硅基流动 / OpenRouter / 中转站 / OpenAI）：
   - LLM_API_KEY：你的 API Key
   - LLM_BASE_URL（可选）：接口地址，如 https://api.deepseek.com/v1 或 https://api.moonshot.cn/v1
   - LLM_MODEL（可选）：模型名称，如 deepseek-chat、gpt-4o-mini、qwen-plus 等
2. 免填 URL 快捷密钥（自动识别厂商与默认模型）：
   - DEEPSEEK_API_KEY（自动走 DeepSeek）
   - OPENAI_API_KEY（自动走 OpenAI）
   - ANTHROPIC_API_KEY（自动走 Claude）
   - GEMINI_API_KEY（自动走 Gemini）
   - 或直接把 AIza... / sk-ant-... / sk-or-... / gsk_... 填到 LLM_API_KEY 中，脚本也会自动识别！
3. 未配置任何 Key 时：自动降级为 RSS 真实新闻直出模式，依然每日更新 data/edition.json。
"""

import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

EDITION_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "edition.json")

RSS_FEEDS = {
    "us": [
        ("NPR News", "https://feeds.npr.org/1001/rss.xml"),
        ("TechCrunch", "https://techcrunch.com/feed/"),
        ("ScienceDaily Top", "https://www.sciencedaily.com/rss/top/science.xml"),
    ],
    "kr": [
        ("Yonhap News", "https://en.yna.co.kr/RSS/news.xml"),
        ("Korea Herald", "https://www.koreaherald.com/rss/020100000000.xml"),
        ("Korea Times", "https://www.koreatimes.co.kr/www/rss/rss.xml"),
    ],
    "eu": [
        ("BBC Europe", "https://feeds.bbci.co.uk/news/world/europe/rss.xml"),
        ("DW Top Stories", "https://rss.dw.com/rdf/rss-en-top"),
        ("Euronews", "https://www.euronews.com/rss?format=mrss&level=theme&name=news"),
    ],
    "other": [
        ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
        ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("UN News", "https://news.un.org/feed/subscribe/en/news/all/rss.xml"),
    ],
    "cn": [
        ("中新网时政", "https://www.chinanews.com.cn/rss/scroll-news.xml"),
        ("中新网财经", "https://www.chinanews.com.cn/rss/finance.xml"),
        ("中新网社会", "https://www.chinanews.com.cn/rss/society.xml"),
    ],
}

TARGET_COUNTS = {"us": 4, "kr": 4, "eu": 4, "other": 3, "cn": 5}
REGION_LABELS = {
    "us": "🇺🇸 美国",
    "kr": "🇰🇷 韩国",
    "eu": "🇪🇺 欧洲",
    "other": "🌍 其他地区",
    "cn": "🇨🇳 国内",
}


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def extract_json_object(raw_text: str) -> dict:
    """兼容各种模型返回的纯 JSON 或带 ```json 代码块包裹的内容。"""
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def fetch_rss(url: str, source_name: str, limit: int = 8) -> list:
    items = []
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "NoonBriefingBot/1.0 (+https://github.com)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
        entries = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
        for entry in entries[:limit]:
            title = (
                entry.findtext("title")
                or entry.findtext("{http://www.w3.org/2005/Atom}title")
                or ""
            ).strip()
            link = (entry.findtext("link") or "").strip()
            if not link:
                link_el = entry.find("{http://www.w3.org/2005/Atom}link")
                if link_el is not None:
                    link = link_el.attrib.get("href", "").strip()
            desc = (
                entry.findtext("description")
                or entry.findtext("{http://www.w3.org/2005/Atom}summary")
                or ""
            )
            pub = (
                entry.findtext("pubDate")
                or entry.findtext("{http://www.w3.org/2005/Atom}updated")
                or ""
            ).strip()
            if title and link.startswith("http"):
                items.append({
                    "source": source_name,
                    "title": strip_html(title),
                    "summary": strip_html(desc)[:320],
                    "url": link,
                    "pubDate": pub,
                })
    except Exception as exc:
        print(f"[WARN] RSS fetch failed for {source_name} ({url}): {exc}", file=sys.stderr)
    return items


def collect_candidates() -> dict:
    pool = {}
    for region, feeds in RSS_FEEDS.items():
        collected = []
        seen_urls = set()
        for name, url in feeds:
            for item in fetch_rss(url, name, limit=8):
                if item["url"] not in seen_urls:
                    seen_urls.add(item["url"])
                    collected.append(item)
        pool[region] = collected[:16]
        print(f"[INFO] Collected {len(pool[region])} candidates for region={region}")
    return pool


def detect_llm_config() -> dict | None:
    """自动检测任意已配置的 API Key，并推断协议类型、接口地址与默认模型。"""
    custom_base = os.environ.get("LLM_BASE_URL", "").strip().rstrip("/")
    custom_model = os.environ.get("LLM_MODEL", "").strip()

    key_candidates = [
        ("LLM_API_KEY", os.environ.get("LLM_API_KEY", "").strip()),
        ("OPENAI_API_KEY", os.environ.get("OPENAI_API_KEY", "").strip()),
        ("DEEPSEEK_API_KEY", os.environ.get("DEEPSEEK_API_KEY", "").strip()),
        ("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY", "").strip()),
        ("GEMINI_API_KEY", os.environ.get("GEMINI_API_KEY", "").strip()),
    ]
    chosen_env, api_key = next(((k, v) for k, v in key_candidates if v), (None, ""))
    if not api_key:
        return None

    # 1. 若用户手动指定了 LLM_BASE_URL，默认走通用 OpenAI 兼容协议（支持几乎所有国内/国外/中转站接口）
    if custom_base:
        return {
            "provider": "openai_compat",
            "api_key": api_key,
            "base_url": custom_base,
            "model": custom_model or "gpt-4o-mini",
            "source_env": chosen_env,
        }

    # 2. 按环境变量名或 Key 前缀自动识别主流服务商
    if chosen_env == "GEMINI_API_KEY" or api_key.startswith("AIza"):
        return {
            "provider": "gemini",
            "api_key": api_key,
            "base_url": "https://generativelanguage.googleapis.com/v1beta",
            "model": custom_model or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash",
            "source_env": chosen_env,
        }

    if chosen_env == "ANTHROPIC_API_KEY" or api_key.startswith("sk-ant-"):
        return {
            "provider": "anthropic",
            "api_key": api_key,
            "base_url": "https://api.anthropic.com/v1",
            "model": custom_model or "claude-3-5-sonnet-latest",
            "source_env": chosen_env,
        }

    if chosen_env == "DEEPSEEK_API_KEY":
        return {
            "provider": "openai_compat",
            "api_key": api_key,
            "base_url": "https://api.deepseek.com/v1",
            "model": custom_model or "deepseek-chat",
            "source_env": chosen_env,
        }

    if api_key.startswith("sk-or-"):
        return {
            "provider": "openai_compat",
            "api_key": api_key,
            "base_url": "https://openrouter.ai/api/v1",
            "model": custom_model or "google/gemini-2.5-flash",
            "source_env": chosen_env,
        }

    if api_key.startswith("gsk_"):
        return {
            "provider": "openai_compat",
            "api_key": api_key,
            "base_url": "https://api.groq.com/openai/v1",
            "model": custom_model or "llama-3.3-70b-versatile",
            "source_env": chosen_env,
        }

    # 默认走 OpenAI 兼容协议
    return {
        "provider": "openai_compat",
        "api_key": api_key,
        "base_url": "https://api.openai.com/v1",
        "model": custom_model or "gpt-4o-mini",
        "source_env": chosen_env,
    }


def build_editor_prompt(pool: dict, today_str: str, next_edition_num: int) -> str:
    return f"""你是《午间万象 · 12:00 每日时政茶馆》的主编。请严格基于下面提供的真实 RSS 新闻候选池，生成 {today_str} 第 {next_edition_num} 期的完整 JSON 数据。

硬性要求：
1. 总计必须正好 20 篇文章，严格满足：
   - region="us" 正好 4 篇（scope="intl"）
   - region="kr" 正好 4 篇（scope="intl"）
   - region="eu" 正好 4 篇（scope="intl"）
   - region="other" 正好 3 篇（scope="intl"）
   - region="cn" 正好 5 篇（scope="domestic"）
   （国际15:国内5 = 3:1；国际中美韩欧12:其他3 = 8:2）
2. 篇幅与阅读时长：总 readMins 之和控制在 24.0 到 27.0 分钟之间。必须包含 4 篇 type="deep"（美/韩/欧/中各1篇，每篇 2.5 分钟）、10 篇 type="news"（1.0-1.5 分钟）、6 篇 type="fun"（0.5-1.0 分钟）。
3. 风格与内容：
   - 用家常化、通俗生动、像午饭聊天一样有趣的中文撰写；保持客观中立，清晰标注消息来源。
   - 不局限于严肃政治，主动挑选候选池里有趣的科技、社会、生态、民生和文化新闻。
   - 每篇必须包含 impactOrdinary（对普通人的影响）和 impactWorld（对世界格局的影响），尤其是 4 篇深度专题要剖析透彻。
   - 每篇 sources 数组中的 url 必须原样复制自下面候选池中的真实链接，严禁编造 URL！
4. 同时生成 2 道当日热点互动投票（polls）。

请只输出严格的纯 JSON 对象（不要加解释文字），结构如下：
{{
  "editionNumber": {next_edition_num},
  "date": "{today_str}",
  "updatedAt": "{today_str} 12:00",
  "headline": "今日一期主标题（25字以内，有温度）",
  "subtitle": "今日导语（80字以内，概括亮点）",
  "articles": [
    {{
      "id": "us-1",
      "region": "us|kr|eu|other|cn",
      "regionLabel": "🇺🇸 美国",
      "scope": "intl|domestic",
      "category": "贸易博弈|硬核科技|宏观钱包|地缘安全|社会百态|生态奇趣|文化跨界|民生烟火",
      "type": "deep|news|fun",
      "readMins": 2.5,
      "date": "{today_str}",
      "title": "...",
      "lead": "...",
      "body": ["第一段...", "第二段..."],
      "impactOrdinary": "...",
      "impactWorld": "...",
      "sources": [{{"name": "来源名", "url": "候选池真实URL"}}]
    }}
  ],
  "polls": [
    {{
      "id": "poll1",
      "question": "...",
      "options": [
        {{"id": "a", "label": "...", "baseVotes": 40}},
        {{"id": "b", "label": "...", "baseVotes": 35}},
        {{"id": "c", "label": "...", "baseVotes": 25}}
      ]
    }}
  ]
}}

真实 RSS 候选池：
{json.dumps(pool, ensure_ascii=False)}
"""


def call_universal_llm(pool: dict, today_str: str, next_edition_num: int) -> dict | None:
    cfg = detect_llm_config()
    if not cfg:
        print("[INFO] No LLM API key found; using RSS fallback builder.")
        return None

    print(f"[INFO] Using provider={cfg['provider']} model={cfg['model']} (from {cfg['source_env']})")
    prompt = build_editor_prompt(pool, today_str, next_edition_num)

    if cfg["provider"] == "gemini":
        endpoint = f"{cfg['base_url']}/models/{cfg['model']}:generateContent?key={cfg['api_key']}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.6, "responseMimeType": "application/json"},
        }
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
        raw_text = res_data["candidates"][0]["content"]["parts"][0]["text"]
        return extract_json_object(raw_text)

    if cfg["provider"] == "anthropic":
        endpoint = f"{cfg['base_url']}/messages"
        payload = {
            "model": cfg["model"],
            "max_tokens": 8192,
            "temperature": 0.6,
            "messages": [{"role": "user", "content": prompt}],
        }
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": cfg["api_key"],
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
        raw_text = "".join(
            block.get("text", "") for block in res_data.get("content", []) if block.get("type") == "text"
        )
        return extract_json_object(raw_text)

    # 通用 OpenAI 兼容协议（OpenAI / DeepSeek / Kimi / 通义千问 / 智谱 / 硅基流动 / OpenRouter / Groq / 中转站）
    base_url = cfg["base_url"]
    endpoint = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    payload = {
        "model": cfg["model"],
        "temperature": 0.6,
        "messages": [
            {"role": "system", "content": "你是专业且风趣的中文时政编辑，只返回合法的 JSON 对象。"},
            {"role": "user", "content": prompt},
        ],
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg['api_key']}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        res_data = json.loads(resp.read().decode("utf-8"))
    raw_text = res_data["choices"][0]["message"]["content"]
    return extract_json_object(raw_text)


def build_fallback_edition(pool: dict, today_str: str, next_edition_num: int) -> dict:
    """无 API Key 时的保底构建器：直接按 3:1 与 8:2 比例组装真实 RSS 资讯。"""
    articles = []
    for region, count in TARGET_COUNTS.items():
        items = pool.get(region, [])[:count]
        for idx, item in enumerate(items, start=1):
            is_deep = idx == 1 and region in ("us", "kr", "eu", "cn")
            is_fun = idx == count and region != "other"
            art_type = "deep" if is_deep else ("fun" if is_fun else "news")
            read_mins = 2.5 if is_deep else (0.5 if is_fun else 1.5)
            articles.append({
                "id": f"{region}-{idx}",
                "region": region,
                "regionLabel": REGION_LABELS[region],
                "scope": "domestic" if region == "cn" else "intl",
                "category": "社会百态" if is_fun else "全球速递",
                "type": art_type,
                "readMins": read_mins,
                "date": today_str,
                "title": item["title"],
                "lead": item["summary"] or f"来自 {item['source']} 的最新报道，点击原文查看完整内容。",
                "body": [
                    item["summary"] or item["title"],
                    f"（提示：在 GitHub 仓库 Secrets 中配置任意大模型 API Key，例如 LLM_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY，即可自动解锁家常化通俗长文与双维深度拆解。原文来源：{item['source']}）",
                ],
                "impactOrdinary": "配置任意大模型 API Key 后将自动生成针对普通人生活、钱包与日常的通俗拆解。",
                "impactWorld": "配置任意大模型 API Key 后将自动生成针对国际格局与产业链的深度分析。",
                "sources": [{"name": item["source"], "url": item["url"]}],
            })

    return {
        "editionNumber": next_edition_num,
        "date": today_str,
        "updatedAt": f"{today_str} 12:00",
        "headline": f"{today_str} 午间万象：全球与国内最新 {len(articles)} 条动态速览",
        "subtitle": "自动通过 GitHub Actions 从全球主流信源抓取更新；配置任意大模型 API Key 即可解锁家常化深度改写与双维拆解。",
        "articles": articles,
        "polls": [
            {
                "id": "poll1",
                "question": "今天这期午间新闻里，你最关注哪个板块？",
                "options": [
                    {"id": "a", "label": "🌍 国际政经与美韩欧动向", "baseVotes": 45},
                    {"id": "b", "label": "🇨🇳 国内民生与科技进展", "baseVotes": 38},
                    {"id": "c", "label": "🎈 轻松有趣的奇闻轶事", "baseVotes": 27},
                ],
            }
        ],
    }


def validate_edition(data: dict) -> bool:
    arts = data.get("articles", [])
    if len(arts) < 12:
        print(f"[ERROR] Too few articles: {len(arts)}", file=sys.stderr)
        return False
    for a in arts:
        if not a.get("title") or not a.get("sources"):
            return False
    return True


def main():
    dry_run = "--dry-run" in sys.argv
    now_cn = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    today_str = now_cn.strftime("%Y-%m-%d")

    prev_num = 330
    if os.path.exists(EDITION_PATH):
        try:
            prev = json.load(open(EDITION_PATH, encoding="utf-8"))
            prev_num = int(prev.get("editionNumber", 330))
            next_num = prev_num if prev.get("date") == today_str else prev_num + 1
        except Exception:
            next_num = prev_num + 1
    else:
        next_num = 1

    pool = collect_candidates()
    total_candidates = sum(len(v) for v in pool.values())
    if total_candidates == 0:
        print("[ERROR] No RSS items fetched; keeping existing edition.json.", file=sys.stderr)
        sys.exit(1)

    edition = None
    try:
        edition = call_universal_llm(pool, today_str, next_num)
    except Exception as exc:
        print(f"[WARN] LLM generation failed ({exc}); falling back to RSS mode.", file=sys.stderr)

    if not edition or not validate_edition(edition):
        edition = build_fallback_edition(pool, today_str, next_num)

    if dry_run:
        print(f"[DRY-RUN] Generated {len(edition['articles'])} articles for {today_str}")
        return

    with open(EDITION_PATH, "w", encoding="utf-8") as f:
        json.dump(edition, f, ensure_ascii=False, indent=2)
    print(f"[OK] Updated {EDITION_PATH} with {len(edition['articles'])} articles ({today_str})")


if __name__ == "__main__":
    main()
