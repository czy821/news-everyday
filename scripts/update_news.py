#!/usr/bin/env python3
"""
午间万象 · 每日自动更新脚本（高可靠分批生成 + 任意大模型 API 自动适配版）
纯 Python 3 标准库，零第三方依赖。
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


def _repair_json_text(text: str) -> str:
    # 去掉对象或数组末尾多余逗号
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


def _extract_balanced_objects(text: str) -> list:
    """当整体 JSON 某个字段有引号/逗号瑕疵时，逐个抢救合法的文章对象。"""
    objs = []
    depth = 0
    in_str = False
    esc = False
    start = -1
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start != -1:
                    chunk = _repair_json_text(text[start : i + 1])
                    try:
                        obj = json.loads(chunk)
                        if isinstance(obj, dict) and obj.get("title"):
                            objs.append(obj)
                    except Exception:
                        pass
                    start = -1
    return objs


def extract_json_object(raw_text: str) -> dict:
    """兼容各种模型返回的纯 JSON、顶层数组 [...]、Markdown 代码块、多余逗号及局部语法瑕疵。"""
    text = (raw_text or "").strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if m:
            text = m.group(1).strip()

    # 找到最外层 { ... } 或 [ ... ]
    obj_start, obj_end = text.find("{"), text.rfind("}")
    arr_start, arr_end = text.find("["), text.rfind("]")
    if arr_start != -1 and (obj_start == -1 or arr_start < obj_start) and arr_end > arr_start:
        text = text[arr_start : arr_end + 1]
    elif obj_start != -1 and obj_end > obj_start:
        text = text[obj_start : obj_end + 1]

    def _normalize_parsed(parsed):
        if isinstance(parsed, list):
            return {"articles": parsed}
        if isinstance(parsed, dict):
            for k in ("articles", "news", "items", "data", "list", "results"):
                if isinstance(parsed.get(k), list):
                    parsed["articles"] = parsed[k]
                    break
            if "articles" not in parsed and parsed.get("title"):
                return {"articles": [parsed]}
            return parsed
        return {"articles": []}

    try:
        return _normalize_parsed(json.loads(text))
    except json.JSONDecodeError:
        repaired = _repair_json_text(text)
        try:
            return _normalize_parsed(json.loads(repaired))
        except json.JSONDecodeError:
            rescued = _extract_balanced_objects(repaired)
            if rescued:
                return {"articles": rescued}
            raise


def invoke_llm_json(cfg: dict, prompt: str) -> dict:
    """向已配置的模型发起单次请求并解析 JSON。"""
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
        with urllib.request.urlopen(req, timeout=90) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
        raw_text = res_data["candidates"][0]["content"]["parts"][0]["text"]
        return extract_json_object(raw_text)

    if cfg["provider"] == "anthropic":
        endpoint = f"{cfg['base_url']}/messages"
        payload = {
            "model": cfg["model"],
            "max_tokens": 4096,
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
        with urllib.request.urlopen(req, timeout=90) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
        raw_text = "".join(
            block.get("text", "") for block in res_data.get("content", []) if block.get("type") == "text"
        )
        return extract_json_object(raw_text)

    base_url = cfg["base_url"]
    endpoint = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
    payload = {
        "model": cfg["model"],
        "max_tokens": 4096,
        "temperature": 0.6,
        "messages": [
            {"role": "system", "content": "你是风趣通俗的中文时政主编，只返回合法的纯 JSON 对象，不要输出任何额外解释。"},
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
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as http_err:
        err_body = ""
        try:
            err_body = http_err.read().decode("utf-8", errors="ignore")[:300]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {http_err.code}: {err_body}") from http_err

    raw_text = res_data["choices"][0]["message"]["content"]
    return extract_json_object(raw_text)


def _normalize_article(a: dict, region: str, idx: int, scope: str, today_str: str, fallback_cand: dict | None) -> dict | None:
    if not isinstance(a, dict):
        return None
    title = str(a.get("title") or "").strip()
    if not title:
        return None

    # 兼容 sources 缺失、字符串或单对象情况，自动回填真实候选池信源
    raw_sources = a.get("sources")
    valid_sources = []
    if isinstance(raw_sources, list):
        for s in raw_sources:
            if isinstance(s, dict) and s.get("url"):
                valid_sources.append({"name": str(s.get("name") or "原文链接"), "url": str(s["url"])})
            elif isinstance(s, str) and s.startswith("http"):
                valid_sources.append({"name": "原文链接", "url": s})
    elif isinstance(raw_sources, dict) and raw_sources.get("url"):
        valid_sources.append({"name": str(raw_sources.get("name") or "原文链接"), "url": str(raw_sources["url"])})

    if not valid_sources and a.get("url") and str(a["url"]).startswith("http"):
        valid_sources.append({"name": str(a.get("source") or "原文链接"), "url": str(a["url"])})
    if not valid_sources and fallback_cand:
        valid_sources.append({"name": fallback_cand["source"], "url": fallback_cand["url"]})
    if not valid_sources:
        return None

    body = a.get("body")
    if isinstance(body, str):
        body = [p.strip() for p in body.split("\n") if p.strip()]
    if not isinstance(body, list) or not body:
        body = [str(a.get("lead") or title)]

    art_type = a.get("type") if a.get("type") in ("deep", "news", "fun") else ("deep" if idx == 1 and region in ("us", "kr", "eu", "cn") else "news")
    try:
        read_mins = float(a.get("readMins") or (2.5 if art_type == "deep" else (0.5 if art_type == "fun" else 1.5)))
    except Exception:
        read_mins = 1.5

    return {
        "id": f"{region}-{idx}",
        "region": region,
        "regionLabel": REGION_LABELS[region],
        "scope": scope,
        "category": str(a.get("category") or "全球速递"),
        "type": art_type,
        "readMins": read_mins,
        "date": today_str,
        "title": title,
        "lead": str(a.get("lead") or body[0]),
        "body": [str(p) for p in body],
        "impactOrdinary": str(a.get("impactOrdinary") or "该事件与日常消费、就业或公共政策息息相关，值得持续留意后续落地细节。"),
        "impactWorld": str(a.get("impactWorld") or "该动向折射出当前区域合作与产业链调整的新趋势。"),
        "sources": valid_sources,
    }


def _generate_single_article(cfg: dict, region: str, cand: dict, idx: int, scope: str, art_type: str, today_str: str) -> dict | None:
    """兜底单篇生成：每次只生成1篇文章，JSON 极短，几乎不可能失败。"""
    read_mins = 2.5 if art_type == "deep" else (0.5 if art_type == "fun" else 1.5)
    prompt = f"""请把下面这条 {REGION_LABELS[region]} 新闻改写成家常化、通俗有趣的中文。
只返回纯 JSON 对象（不要用中文以外的引号做键名，正文引号请用“”）：
{{
  "title": "通俗有趣的中文标题",
  "category": "贸易博弈|硬核科技|宏观钱包|地缘安全|社会百态|生态奇趣|文化跨界|民生烟火",
  "lead": "一句话拉家常导语",
  "body": ["第一段通俗讲解", "第二段补充背景"],
  "impactOrdinary": "对普通人生活或钱包的具体影响",
  "impactWorld": "对世界格局或行业趋势的影响"
}}
新闻素材：{json.dumps(cand, ensure_ascii=False)}"""
    res = invoke_llm_json(cfg, prompt)
    arts = res.get("articles") or ([res] if res.get("title") else [])
    if not arts:
        return None
    item = arts[0]
    item["type"] = art_type
    item["readMins"] = read_mins
    item["sources"] = [{"name": cand["source"], "url": cand["url"]}]
    return _normalize_article(item, region, idx, scope, today_str, cand)


def generate_region_articles(cfg: dict, region: str, candidates: list, count: int, today_str: str) -> list:
    """按区域生成 + 缺失槽位单篇补全，确保 100% 产出有效中文深度解析。"""
    if not candidates:
        return []
    scope = "domestic" if region == "cn" else "intl"
    has_deep = region in ("us", "kr", "eu", "cn")
    deep_rule = f"其中第 1 篇必须为 type='deep'（readMins=2.5），其余 {count-1} 篇混合 type='news'（1.0-1.5分钟）和 type='fun'（0.5-1.0分钟）。" if has_deep else f"全部 {count} 篇混合 type='news'（1.0-1.5分钟）和 type='fun'（0.5-1.0分钟）。"

    prompt = f"""请从下面 {REGION_LABELS[region]} 真实新闻候选池中精选 {count} 条，用家常化、通俗生动、像午饭聊天一样有趣的中文改写。
要求：
1. {deep_rule}
2. 每篇必须有中文 title、lead（一句话拉家常导语）、body（2-3段通俗正文的数组）、impactOrdinary（对普通人的影响，具体贴近生活）、impactWorld（对世界格局的影响，有深度洞察）。
3. sources 中的 url 必须原样复制自候选池，严禁编造链接！
4. 【JSON格式铁律】正文和标题中如需引用，请一律使用中文全角引号“”或「」，严禁在字符串内部出现未转义的英文双引号 "，也不要在末尾加多余逗号。

只输出纯 JSON：
{{
  "articles": [
    {{
      "id": "{region}-1",
      "region": "{region}",
      "regionLabel": "{REGION_LABELS[region]}",
      "scope": "{scope}",
      "category": "贸易博弈|硬核科技|宏观钱包|地缘安全|社会百态|生态奇趣|文化跨界|民生烟火",
      "type": "deep|news|fun",
      "readMins": 1.5,
      "date": "{today_str}",
      "title": "...",
      "lead": "...",
      "body": ["第一段...", "第二段..."],
      "impactOrdinary": "...",
      "impactWorld": "...",
      "sources": [{{"name": "来源名", "url": "候选池真实URL"}}]
    }}
  ]
}}

候选池：
{json.dumps(candidates, ensure_ascii=False)}
"""
    cleaned = []
    for attempt in range(2):
        try:
            res = invoke_llm_json(cfg, prompt)
            raw_arts = res.get("articles", []) if isinstance(res, dict) else []
            temp = []
            for idx, a in enumerate(raw_arts[:count], start=1):
                cand = candidates[(idx - 1) % len(candidates)]
                norm = _normalize_article(a, region, idx, scope, today_str, cand)
                if norm:
                    temp.append(norm)
            if temp:
                cleaned = temp
                break
        except Exception as exc:
            print(f"[INFO] Batch attempt {attempt+1} for {region} had error: {exc}")

    # 若批量返回不足 count 篇（或为 0 篇），自动逐篇补全剩余槽位
    used_urls = {s["url"] for a in cleaned for s in a.get("sources", [])}
    remaining_cands = [c for c in candidates if c["url"] not in used_urls] or candidates
    cand_cursor = 0
    while len(cleaned) < min(count, len(candidates)) and cand_cursor < len(remaining_cands):
        slot_idx = len(cleaned) + 1
        art_type = "deep" if (slot_idx == 1 and has_deep) else ("fun" if slot_idx == count and region != "other" else "news")
        cand = remaining_cands[cand_cursor]
        cand_cursor += 1
        try:
            single = _generate_single_article(cfg, region, cand, slot_idx, scope, art_type, today_str)
            if single:
                cleaned.append(single)
        except Exception as exc:
            print(f"[WARN] Single-article fallback failed for {region}-{slot_idx}: {exc}")

    return cleaned


def build_fallback_articles_for_region(region: str, candidates: list, count: int, today_str: str, reason: str) -> list:
    items = candidates[:count]
    out = []
    for idx, item in enumerate(items, start=1):
        is_deep = idx == 1 and region in ("us", "kr", "eu", "cn")
        is_fun = idx == count and region != "other"
        art_type = "deep" if is_deep else ("fun" if is_fun else "news")
        read_mins = 2.5 if is_deep else (0.5 if is_fun else 1.5)
        out.append({
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
                f"（AI 深度改写暂未触发，原因：{reason}。原文来源：{item['source']}）",
            ],
            "impactOrdinary": f"待 AI 解锁（原因：{reason}）。",
            "impactWorld": f"待 AI 解锁（原因：{reason}）。",
            "sources": [{"name": item["source"], "url": item["url"]}],
        })
    return out


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
    if sum(len(v) for v in pool.values()) == 0:
        print("[ERROR] No RSS items fetched; keeping existing edition.json.", file=sys.stderr)
        sys.exit(1)

    cfg = detect_llm_config()
    if cfg:
        print(f"[INFO] Detected LLM config: provider={cfg['provider']} model={cfg['model']} base_url={cfg['base_url']} env={cfg['source_env']}")
    else:
        print("[WARN] No LLM API key detected in environment variables.")

    all_articles = []
    errors = []
    ai_success_count = 0

    for region, count in TARGET_COUNTS.items():
        candidates = pool.get(region, [])
        if cfg:
            try:
                arts = generate_region_articles(cfg, region, candidates, count, today_str)
                if len(arts) >= max(1, count - 1):
                    all_articles.extend(arts)
                    ai_success_count += len(arts)
                    print(f"[OK] AI generated {len(arts)} articles for {region}")
                    continue
                raise RuntimeError(f"AI returned only {len(arts)} valid articles")
            except Exception as exc:
                err_msg = str(exc)
                print(f"[WARN] AI failed for {region}: {err_msg}", file=sys.stderr)
                errors.append(f"{region}: {err_msg}")
                all_articles.extend(build_fallback_articles_for_region(region, candidates, count, today_str, err_msg))
        else:
            reason = "未检测到 Secrets 中的 API Key（请确认 Secret 名称为 LLM_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY 等）"
            all_articles.extend(build_fallback_articles_for_region(region, candidates, count, today_str, reason))

    # 生成主标题与互动投票
    headline = f"{today_str} 午间万象：全球与国内最新 {len(all_articles)} 条动态"
    subtitle = "端杯热茶，20–30分钟聊透今天全球与身边的大小事：国际国内3:1、美韩欧8:2。"
    polls = [
        {
            "id": "poll1",
            "question": "今天这期午间新闻里，你最关注哪个板块？",
            "options": [
                {"id": "a", "label": "🌍 国际政经与美韩欧动向", "baseVotes": 45},
                {"id": "b", "label": "🇨🇳 国内民生与科技进展", "baseVotes": 38},
                {"id": "c", "label": "🎈 轻松有趣的奇闻轶事", "baseVotes": 27},
            ],
        }
    ]

    if cfg and ai_success_count > 0:
        try:
            titles_preview = [a["title"] for a in all_articles[:10]]
            meta_prompt = f"""基于今天已选的头条标题：{json.dumps(titles_preview, ensure_ascii=False)}
请生成本期《午间万象》的主标题、导语和2道有趣的茶馆互动投票。只输出纯 JSON：
{{
  "headline": "25字以内有温度的主标题",
  "subtitle": "80字以内家常化导语",
  "polls": [
    {{
      "id": "poll1",
      "question": "投票问题1",
      "options": [
        {{"id": "a", "label": "选项1", "baseVotes": 42}},
        {{"id": "b", "label": "选项2", "baseVotes": 36}},
        {{"id": "c", "label": "选项3", "baseVotes": 22}}
      ]
    }},
    {{
      "id": "poll2",
      "question": "投票问题2",
      "options": [
        {{"id": "a", "label": "选项1", "baseVotes": 51}},
        {{"id": "b", "label": "选项2", "baseVotes": 31}},
        {{"id": "c", "label": "选项3", "baseVotes": 18}}
      ]
    }}
  ]
}}"""
            meta = invoke_llm_json(cfg, meta_prompt)
            headline = meta.get("headline") or headline
            subtitle = meta.get("subtitle") or subtitle
            if isinstance(meta.get("polls"), list) and len(meta["polls"]) >= 1:
                polls = meta["polls"]
        except Exception as exc:
            print(f"[WARN] Meta/polls generation failed: {exc}", file=sys.stderr)
    elif errors:
        subtitle = f"部分板块 AI 调用异常（{errors[0][:80]}），已自动保留原文链接。"

    edition = {
        "editionNumber": next_num,
        "date": today_str,
        "updatedAt": f"{today_str} 12:00",
        "headline": headline,
        "subtitle": subtitle,
        "articles": all_articles,
        "polls": polls,
    }

    if dry_run:
        print(f"[DRY-RUN] Generated {len(all_articles)} articles (AI={ai_success_count}) for {today_str}")
        return

    with open(EDITION_PATH, "w", encoding="utf-8") as f:
        json.dump(edition, f, ensure_ascii=False, indent=2)
    print(f"[OK] Updated {EDITION_PATH} with {len(all_articles)} articles (AI={ai_success_count}) for {today_str}")


if __name__ == "__main__":
    main()
