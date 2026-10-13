#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""招聘信息采集器 — 主通道:智联招聘(免登录) / 扩展通道:需登录站点

用法:
    # 1) 抓取(默认智联,免登录)
    python scrape_jobs.py --keyword "Java开发" --city 北京 --pages 3 --limit 60 --out ./out

    # 2) 多关键词
    python scrape_jobs.py --keyword "Java开发" --keyword "后端工程师" --city 深圳 --out ./out

    # 3) 首次登录扩展站点(有头浏览器,用户扫码一次,之后复用 profile)
    python scrape_jobs.py --login --site zhipin

    # 4) 带登录态抓 BOSS直聘(尽力而为)
    python scrape_jobs.py --keyword "Java开发" --city 北京 --site zhipin --out ./out

产出:
    out/jobs.json        结构化岗位数组
    out/raw/{slug}.json  每个关键词+站点的原始抓取记录(便于断点与排障)

设计要点:
    - 真实浏览器(bundled chromium),不用 requests 硬打接口
    - 每站之间 sleep,单站点内部滚动限速,降低被抓风险
    - 断点续跑:raw/{slug}.json 已存在且非空则跳过,除非 --refresh
    - 字段缺失一律留 null 并记 degraded 标记,绝不编造
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

# playwright 只在真正抓取时才需要,故惰性导入。
# 这样 --check-sources(纯静态声明)与 --help 在未安装 playwright 的环境中也能运行,
# 便于 CI 与快速自检。
try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

# ---------------------------------------------------------------- 常量与映射

DEFAULT_CHROME = r"C:\Users\Administrator\AppData\Local\ms-playwright\chromium-1234\chrome-win64\chrome.exe"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 智联招聘城市码 (jl) —— 仅收录已核实的常见城市,未知城市回退为城市名搜索
ZHAOPIN_CITY = {
    "全国": "", "北京": "530", "上海": "538", "广州": "763", "深圳": "765",
    "杭州": "653", "成都": "801", "南京": "635", "武汉": "736", "西安": "854",
    "苏州": "639", "天津": "531", "重庆": "551", "长沙": "749", "郑州": "719",
    "青岛": "637", "合肥": "664", "宁波": "654", "东莞": "766", "佛山": "768",
    "济南": "702", "福州": "681", "厦门": "682", "大连": "600", "沈阳": "599",
    "昆明": "831", "无锡": "640", "南昌": "786", "贵阳": "874", "石家庄": "5383",
}

SITES = {
    "zhaopin": {
        "name": "智联招聘",
        "access": "public",           # public=免登录 / login=需登录
        "needs_login": False,
        "structured": True,           # 能拿到完整 JD/技能/公司
        "search": "https://www.zhaopin.com/jobs?jl={city_code}&kw={kw}",
        # 按优先级探测,任一命中即可;站点改版时把新选择器补到前面
        "card_candidates": [
            ".job-card",
            ".joblist-box__item",
            "[class*=joblist-box] [class*=item]",
            ".joblist-item",
            ".positionlist .item",
            "[class*=job-card]",
        ],
    },
    "51job": {
        "name": "前程无忧",
        "access": "public",
        "needs_login": False,
        "structured": True,
        "search": "https://we.51job.com/pc/search?jobArea={city_code}&keyword={kw}&pageNum={page}",
        "card_candidates": [
            ".joblist-item",
            "[class*=joblist] [class*=item]",
            ".j_joblist .e",
        ],
    },
    "mohrss": {
        "name": "中国公共招聘网(人社部)",
        "access": "public",
        "needs_login": False,
        "structured": False,          # 只有岗位名/单位/薪资档/地区
        "search": "http://job.mohrss.gov.cn/cjobs/jobinfolist/listJobinfolist?pageNo={page}",
        "card_candidates": ["[class*=list] li", "li"],
    },
    "zhipin": {
        "name": "BOSS直聘",
        "access": "login",
        "needs_login": True,
        "structured": True,
        "search": "https://www.zhipin.com/web/geek/job?query={kw}&city={city_code}",
        "card_candidates": [
            ".job-card-wrapper", ".job-card-box", "li.job-card-wrapper",
            ".job-list-box .job-card-wrapper", "[class*=job-card]",
        ],
    },
}

# 实测不可用的源(记录原因,便于文档说明与将来复测)
UNAVAILABLE_SOURCES = {
    "liepin": {"name": "猎聘", "reason": "搜索页返回 about:blank,强风控"},
    "maimai": {"name": "脉脉", "reason": "302 跳扫码登录页,必须登录"},
    "linkedin": {"name": "领英", "reason": "国内网络 ERR_HTTP_RESPONSE_CODE_FAILURE"},
    "lagou": {"name": "拉勾", "reason": "滑动验证码拦截"},
    "bjrc": {"name": "北京人才网", "reason": "域名已失效 ERR_NAME_NOT_RESOLVED"},
}

# 各站点城市码(仅收录已核实的城市,未知则回退为全国搜索)
ZHAOPIN_CITY = {
    "全国": "", "北京": "530", "上海": "538", "广州": "763", "深圳": "765",
    "杭州": "653", "成都": "801", "南京": "635", "武汉": "736", "西安": "854",
    "苏州": "639", "天津": "531", "重庆": "551", "长沙": "749", "郑州": "719",
    "青岛": "637", "合肥": "664", "宁波": "654", "东莞": "766", "佛山": "768",
    "济南": "702", "福州": "681", "厦门": "682", "大连": "600", "沈阳": "599",
    "昆明": "831", "无锡": "640", "南昌": "786", "贵阳": "874", "石家庄": "5383",
}

# 前程无忧 jobArea 码(6 位)
JOB51_CITY = {
    "全国": "", "北京": "010000", "上海": "020000", "广州": "030200",
    "深圳": "040000", "杭州": "080200", "成都": "090200", "南京": "070200",
    "武汉": "180200", "西安": "200200", "苏州": "070300", "天津": "050000",
    "重庆": "060000", "长沙": "190200", "郑州": "170200", "青岛": "120300",
    "合肥": "150200", "宁波": "080300", "东莞": "030800", "佛山": "030600",
}

BOSS_CITY = {"全国": "100010000", "北京": "101010100", "上海": "101020100",
             "广州": "101280100", "深圳": "101280600", "杭州": "101210100",
             "成都": "101270100", "南京": "101190100", "武汉": "101200100"}

MASK_SALARY = re.compile(r"\*+")

# ---------------------------------------------------------------- 工具函数


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def slugify(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff-]+", "_", text).strip("_")[:60]


def clean(text: str | None) -> str | None:
    if text is None:
        return None
    out = re.sub(r"\s+", " ", str(text)).strip()
    return out or None


def detect_chrome(cli_path: str | None) -> str:
    """定位 chromium 可执行文件(跨平台)。

    顺序:显式 --chrome > 内置默认路径 > ms-playwright 缓存目录下的任意 chromium。
    """
    if cli_path:
        p = Path(cli_path)
        if p.exists():
            return str(p)
        raise SystemExit(f"指定的浏览器不存在: {cli_path}")
    if Path(DEFAULT_CHROME).exists():
        return DEFAULT_CHROME

    # ms-playwright 缓存目录:按平台区分文件名
    home = Path.home()
    if sys.platform.startswith("win"):
        bases = [home / "AppData" / "Local" / "ms-playwright"]
        patterns = ["chromium-*/chrome-win64/chrome.exe",
                    "chromium-*/chrome-win/chrome.exe"]
    elif sys.platform == "darwin":
        bases = [home / "Library" / "Caches" / "ms-playwright"]
        patterns = ["chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium"]
    else:
        bases = [home / ".cache" / "ms-playwright"]
        patterns = ["chromium-*/chrome-linux*/chrome"]

    for base in bases:
        for pat in patterns:
            for cand in sorted(base.glob(pat), reverse=True):
                if cand.exists():
                    return str(cand)

    raise SystemExit(
        "未找到 chromium。请先执行 `playwright install chromium`,"
        "或用 --chrome 指定浏览器路径。")



def is_logged_in(page) -> bool:
    """粗判当前站点是否处于登录态。"""
    try:
        url = page.url
        if re.search(r"/web/user|/login|passport|/user/login", url):
            return False
        html = page.content()[:20000]
        if re.search(r"扫码登录|短信登录|立即登录|账号登录", html):
            return False
        return True
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- 解析器

EDU_TOKENS = ("博士", "硕士", "本科", "大专", "中专", "高中", "初中", "学历不限", "不限")
EXP_TOKENS = ("应届", "经验不限", "在校", "1年以内", "1-3年", "3-5年", "5-10年", "10年以上")
# 薪资串形态(智联/通用):
#   1.5-1.8万       -> 15000 ~ 18000
#   8000-16000元    -> 8000 ~ 16000
#   3000-4000元     -> 3000 ~ 4000
#   15-25K / 15-25k -> 15000 ~ 25000
#   2-4万·15薪      -> 20000 ~ 40000,月数 15
SALARY_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(万|千|[kK])?\s*[-~至]\s*(\d+(?:\.\d+)?)\s*(万|千|[kK])?\s*(?:元)?"
)


def _unit_mult(unit: str | None) -> float | None:
    if unit == "万":
        return 10000.0
    if unit in ("千", "k", "K"):
        return 1000.0
    return None


def _sal_range(text: str) -> tuple[float, float] | None:
    """解析薪资区间为 (min, max),单位:元/月。

    单位推断规则(顺序很重要):
      1. 两侧任一显式带单位 -> 该单位为基准,另一侧沿用同一单位
      2. 无显式单位,但出现「·N薪」        -> 按 万
      3. 无显式单位,出现「元」            -> 按 元
      4. 无显式单位,数字 < 100            -> 按 K(如 15-25)
      5. 无显式单位,数字 >= 1000          -> 按 元(如 8000-16000)
      6. 其余 -> 放弃(不猜,避免污染薪资统计)

    防御:
      - 排除「3-5年」「1-3年」这类经验年限,它们不是薪资
      - **年薪换算**:出现「/年」「万/年」「年薪」时,折算成月薪(÷12)
    """
    if not text:
        return None
    # 年限/次数误判防护:紧跟「年/个月/天/人/薪」的数字区间不是薪资
    if re.search(r"\d+\s*[-~至]\s*\d+\s*(?:年|个月|月|天|周|人|次|条|个)", text) \
            and not re.search(r"(?:万|千|[kK]|元)", text):
        return None
    m = SALARY_RE.search(text)
    if not m:
        return None

    a, ua, b, ub = m.group(1), m.group(2), m.group(3), m.group(4)
    try:
        va, vb = float(a), float(b)
    except ValueError:
        return None

    # 两侧**各自**定单位:支持「9千-1.5万」这类混用写法
    ma, mb = _unit_mult(ua), _unit_mult(ub)
    if ma is None and mb is None:
        # 都没写单位 -> 按整条文本推断
        if re.search(r"\d{1,2}\s*薪", text):
            ma = mb = 10000.0
        elif "元" in text:
            ma = mb = 1.0
        elif max(va, vb) < 100:
            ma = mb = 1000.0
        elif min(va, vb) >= 1000:
            ma = mb = 1.0
        else:
            return None   # 单位不明,宁缺毋滥
    elif ma is None:
        ma = mb          # 左侧缺单位 -> 沿用右侧
    elif mb is None:
        mb = ma          # 右侧缺单位 -> 沿用左侧

    lo, hi = va * ma, vb * mb

    # 年薪换算:出现「/年」「万/年」「年薪」等 -> 折成月薪,避免与月薪样本混算
    if re.search(r"(?:年\s*薪|/\s*年|每年|年薪)", text):
        lo, hi = lo / 12.0, hi / 12.0

    if lo <= 0 or hi <= 0 or hi < lo:
        return None
    # 合理性护栏:月薪 800 ~ 200 万,超出视为解析错误
    if not (800 <= lo <= 2_000_000) or not (800 <= hi <= 2_000_000):
        return None
    return (lo, hi)


def _parse_card_text(text: str, *, source: str, city: str) -> dict | None:
    """从卡片整段文本解析(选择器失效时的兜底,也用于扩展站点)。

    与结构化解析共用同一套口径,保证两条路径产出字段一致。
    """
    text = clean(text)
    if not text:
        return None
    parts = [p for p in re.split(r"[\s|·]+", text) if p]
    if not parts:
        return None

    title = None
    for p in parts[:3]:
        if not SALARY_RE.search(p) and not any(k in p for k in EDU_TOKENS) \
                and not any(k in p for k in EXP_TOKENS) and len(p) >= 2:
            title = p
            break
    if not title:
        return None

    sal = _sal_range(text)
    return {
        "title": title,
        "salary_raw": None,
        "salary_min": sal[0] if sal else None,
        "salary_max": sal[1] if sal else None,
        "education": next((p for p in parts if any(k in p for k in EDU_TOKENS)), None),
        "experience": next((p for p in parts if any(k in p for k in EXP_TOKENS)), None),
        "skills": [],
        "company": None,
        "company_size": None,
        "company_stage": None,
        "industry": None,
        "location": city or None,
        "url": None,
        "source": source,
        "requirements_text": text[:400],
        "degraded": ["文本兜底解析:字段完整度较低"],
    }


def parse_zhaopin_card(card) -> dict | None:
    """解析单张智联岗位卡片。字段取不到则 None,绝不编造。"""
    try:
        title = None
        for sel in (".vue-clamp__text", ".job-card__title-main", "[aria-label]"):
            loc = card.locator(sel).first
            if loc.count():
                title = loc.get_attribute("aria-label") or loc.inner_text()
                title = clean(title)
                if title:
                    break
        if not title:
            return None

        salary = clean(card.locator(".job-card__salary").first.inner_text()) \
            if card.locator(".job-card__salary").count() else None
        if salary and MASK_SALARY.search(salary):
            salary = None  # 列表页常见掩码 **-**元,不使用

        tags = []
        for i in range(card.locator(".job-card__skill-tag").count()):
            t = clean(card.locator(".job-card__skill-tag").nth(i).inner_text())
            if t:
                tags.append(t)

        edu = next((t for t in tags if any(k in t for k in EDU_TOKENS)), None)
        exp = next((t for t in tags if any(k in t for k in EXP_TOKENS)), None)
        skills = [t for t in tags if t not in (edu, exp)]

        company = None
        if card.locator(".job-card__company-name").count():
            company = clean(card.locator(".job-card__company-name").first.inner_text())

        location = None
        loc_span = card.locator(".job-card__location span").first
        if card.locator(".job-card__location span").count():
            location = clean(loc_span.get_attribute("title") or loc_span.inner_text())

        job_url = None
        anchors = card.locator("a")
        for i in range(anchors.count()):
            href = anchors.nth(i).get_attribute("href") or ""
            if "jobdetail" in href:
                job_url = href.split("&")[0] if href.startswith("http") else None
                break

        # 薪资:卡片内 salary 节点优先;被掩码则整卡文本兜底
        card_text = clean(card.inner_text()) or ""
        sal = None
        if salary:
            sal = _sal_range(salary)
        if sal is None:
            sal = _sal_range(card_text)

        degraded = []
        if sal is None:
            degraded.append("薪资未获取(掩码或未展示)")
        if not edu:
            degraded.append("学历未标注")

        return {
            "title": title,
            "salary_raw": salary,
            "salary_min": sal[0] if sal else None,
            "salary_max": sal[1] if sal else None,
            "education": edu,
            "experience": exp,
            "skills": _filter_skills(skills),
            "company": company,
            "company_size": None,     # 列表页无,详情页补
            "company_stage": None,
            "industry": None,
            "location": location,
            "url": job_url,
            "source": "智联招聘",
            "requirements_text": card_text[:400] or None,
            "degraded": degraded,
        }
    except Exception as e:  # noqa: BLE001
        log(f"  卡片解析失败: {type(e).__name__}: {e}")
        # 最后兜底:整卡文本解析
        try:
            return _parse_card_text(card.inner_text(), source="智联招聘", city="")
        except Exception:  # noqa: BLE001
            return None


def resolve_card_selector(page, candidates: list[str], min_hits: int = 3) -> str | None:
    """从候选选择器里挑出真正能命中岗位卡片的那个。

    站点改版时选择器会漂移,这里不写死单一选择器,而是按顺序探测,
    取第一个命中数 >= min_hits 的。探测结果会打日志便于排障。
    """
    best = None
    for sel in candidates:
        try:
            n = page.locator(sel).count()
        except Exception:  # noqa: BLE001
            continue
        if n >= min_hits:
            log(f"  卡片选择器命中: {sel} -> {n}")
            return sel
        if n and best is None:
            best = sel
    if best:
        log(f"  卡片选择器弱命中: {best}")
    return best


def harvest_page_salaries(page) -> list[str]:
    """列表页 HTML 中可能残留真实薪资串,作为薪资样本补充来源。"""
    try:
        html = page.content()
        return re.findall(r"\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?\s*(?:[kK]|万|元|千)", html)
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------- 抓取主流程


def scrape_zhaopin(page, keyword: str, city: str, pages: int, limit: int,
                   scroll_rounds: int) -> tuple[list[dict], dict]:
    """抓取智联招聘。

    主路径:从 `window.__INITIAL_STATE__` 抽取结构化岗位数据。
    该对象含真实薪资(列表页 DOM 的 **-**元 只是掩码)、完整 JD、学历、经验、
    技能标签、公司名、工作地点。比解析 DOM 更完整且更抗改版。
    兜底路径:结构化抽取为空时,退回 DOM 卡片解析。
    """
    code = ZHAOPIN_CITY.get(city, "")
    url = SITES["zhaopin"]["search"].format(city_code=code, kw=quote(keyword))
    meta = {"site": "智联招聘", "keyword": keyword, "city": city,
            "search_url": url, "login_wall": False, "salary_samples": [],
            "extract_mode": None, "notes": []}
    jobs: list[dict] = []
    seen: set[str] = set()

    for pno in range(1, pages + 1):
        page_url = url if pno == 1 else f"{url}&p={pno}"
        log(f"智联 | {keyword} | {city} | 第 {pno}/{pages} 页")
        try:
            page.goto(page_url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:  # noqa: BLE001
            meta["notes"].append(f"第{pno}页导航失败: {type(e).__name__}")
            log("  导航失败,跳过该页")
            continue
        page.wait_for_timeout(5000 + random.randint(0, 1500))

        if "user" in page.url and not is_logged_in(page):
            meta["login_wall"] = True
            meta["notes"].append("命中登录墙,智联该次未取到数据")
            log("  命中登录墙,终止智联采集")
            break

        # 滚动触发懒加载,状态树才会补齐后续卡片
        for _ in range(scroll_rounds):
            page.mouse.wheel(0, 1800)
            page.wait_for_timeout(900 + random.randint(0, 600))

        # --- 主路径:结构化状态树(薪资/JD/学历/经验最全) ---
        records = extract_zhaopin_state(page)
        if records:
            meta["extract_mode"] = "initial_state"
            # 状态树的 company 分支常为空,用 DOM 卡片的公司名/地点补全
            dom_map = _zhaopin_dom_index(page)
            if dom_map:
                filled = 0
                for rec in records:
                    key = f"{rec.get('title')}|{rec.get('location')}"
                    extra = dom_map.get(rec.get("title"))
                    if extra:
                        if not rec.get("company"):
                            rec["company"] = extra.get("company")
                        if not rec.get("company_size"):
                            rec["company_size"] = extra.get("company_size")
                        if not rec.get("location"):
                            rec["location"] = extra.get("location")
                        if extra.get("company") and not rec.get("company"):
                            filled += 1
                    if rec.get("company"):
                        rec["degraded"] = [d for d in rec["degraded"] if d != "公司名未获取"]
                meta["notes"].append(f"DOM 补全公司信息: {sum(1 for r in records if r.get('company'))}/{len(records)}")
        else:
            meta["extract_mode"] = meta["extract_mode"] or "dom"
            meta["notes"].append(f"第{pno}页状态树为空,退回 DOM 解析")
            records = extract_zhaopin_dom(page, city)

        log(f"  抽取 {len(records)} 条")
        for rec in records:
            key = f"{rec['title']}|{rec.get('company')}|{rec.get('location')}"
            if key in seen:
                continue
            seen.add(key)
            jobs.append(rec)
            if len(jobs) >= limit:
                break

        meta["salary_samples"].extend(harvest_page_salaries(page)[:40])
        if len(jobs) >= limit:
            log(f"  已达上限 {limit},停止翻页")
            break
        if not records:
            break
        time.sleep(2.5 + random.random() * 2)

    return jobs, meta


# 智联 desc.labels 里混入了行业名、工作方式等非技能标签,需剔除
INDUSTRY_RE = re.compile(
    r"(服务$|行业$|^软件/|^互联网|/IT$|/软件$|/硬件$|/电子$|/通信$|"
    r"^通信/|^半导体/|/芯片$|^半导体|^电子/|^家电|^家具|^建材|^服装|"
    r"^教育(科技|软件|培训)|^金融|^医疗|^制造|^零售|^物流|^地产|^能源|^环保|"
    r"^传媒|^广告|^汽车|^新能源|^人工智能$|^电子商务$|^云计算$|^物联网$|"
    r"^批发|^贸易|^消费电子|^智能硬件$)"
)
BENEFIT_RE = re.compile(
    r"(早九晚六|双休|五险一金|六险一金|包吃|包住|弹性工作|不加班|"
    r"免费班车|节日福利|定期体检|带薪年假|年终奖|股票期权|加班补助)"
)
DESC_PHRASE_RE = re.compile(
    r"(^[\u4e00-\u9fff]{0,6}项目$|^业务系统$|^前后端|^通用开发$|^嵌入式开发$|"
    r"^跨团队|^人力资源$|^推荐$|^搜索$)"
)


def _filter_skills(labels: list[str]) -> list[str]:
    """从智联 labels 中筛出真正的技能/能力标签。

    必须剔除三类混入物:
      1. 行业名(「软件/IT服务」「通信/网络设备」「半导体/芯片」)
      2. 福利/工作方式(「早九晚六」「双休」「五险一金」)
      3. 描述性短语(「车载项目」「业务系统」「跨团队资源协同」)
    但**不能只保留英文**——产品/运营/设计类岗位的标签本身就是中文
    (「需求分析」「用户研究」「Axure」),过度过滤会让这些岗位无标签可用。
    含「/」的标签几乎都是行业分类,直接剔除。
    """
    out = []
    for s in labels or []:
        s = (s or "").strip()
        if not s or len(s) > 24:
            continue
        if "/" in s:                       # 行业分类形态,如「通信/网络设备」
            continue
        if INDUSTRY_RE.search(s) or BENEFIT_RE.search(s) or DESC_PHRASE_RE.search(s):
            continue
        # 英文/技术词直接保留
        if re.search(r"[A-Za-z]", s):
            out.append(s)
            continue
        # 纯中文:长度 2–12 且不像行业/福利 -> 视为能力标签
        if 2 <= len(s) <= 12:
            out.append(s)
    return out


def _zhaopin_dom_index(page) -> dict[str, dict]:
    """建立「岗位名 -> {公司, 公司规模, 地点}」索引,用于补全状态树缺失字段。

    智联列表页左侧卡片区往往带公司名;同页多张卡片同名岗位时取首个非空值。
    """
    idx: dict[str, dict] = {}
    try:
        data = page.evaluate("""() => {
            const out = [];
            const sels = ['.job-card', '[class*=job-card]'];
            let cards = [];
            for (const s of sels) {
                const n = document.querySelectorAll(s);
                if (n.length >= 3) { cards = Array.from(n); break; }
            }
            for (const c of cards) {
                const q = (sel) => {
                    const el = c.querySelector(sel);
                    return el ? (el.getAttribute('title') || el.innerText || '') : '';
                };
                const titleEl = c.querySelector('.vue-clamp__text') ||
                                c.querySelector('[aria-label]');
                const title = titleEl
                    ? (titleEl.getAttribute('aria-label') || titleEl.innerText || '')
                    : '';
                out.push({
                    title: title.trim(),
                    company: q('.job-card__company-name').trim(),
                    size: q('[class*=company-size]').trim(),
                    location: q('.job-card__location span').trim(),
                });
            }
            return out;
        }""")
    except Exception:  # noqa: BLE001
        return idx
    for row in data or []:
        t = (row.get("title") or "").strip()
        if not t:
            continue
        rec = {
            "company": clean(row.get("company")),
            "company_size": clean(row.get("size")),
            "location": clean(row.get("location")),
        }
        if t in idx:
            for k, v in rec.items():
                if v and not idx[t].get(k):
                    idx[t][k] = v
        else:
            idx[t] = rec
    return idx


def extract_zhaopin_state(page) -> list[dict]:
    """从 window.__INITIAL_STATE__ 递归抽取岗位节点。"""
    try:
        raw = page.evaluate("""() => {
            const st = window.__INITIAL_STATE__;
            if (!st) return [];
            const out = [];
            const seen = new Set();
            function walk(o, d) {
                if (d > 10 || !o || typeof o !== 'object') return;
                if (Array.isArray(o)) { o.forEach(v => walk(v, d + 1)); return; }
                const b = o.base;
                if (b && b.positionName && b.positionId && !seen.has(b.positionId)) {
                    seen.add(b.positionId);
                    const desc = o.desc || {};
                    const loc = o.workLocation || {};
                    out.push({
                        positionId: b.positionId,
                        positionName: b.positionName,
                        salary: b.salary || null,
                        education: b.education || null,
                        experience: b.positionWorkingExp || null,
                        workType: b.workType || null,
                        publishText: (o.date || {}).positionUpdateTimeText || null,
                        description: desc.description || null,
                        labels: desc.labels || [],
                        welfare: desc.welfareTags || desc.welfareLabel || [],
                        industry: (o.industry || {}).industryName || o.industryName || null,
                        companyName: (o.company || {}).name || (o.company || {}).companyName || null,
                        companySize: (o.company || {}).companySize || null,
                        companyStage: (o.company || {}).financingStage || null,
                        workAddress: loc.workAddress || loc.address || null,
                        salaryReal: b.salaryReal || null,
                    });
                }
                for (const k of Object.keys(o)) walk(o[k], d + 1);
            }
            walk(st, 0);
            return out;
        }""")
    except Exception as e:  # noqa: BLE001
        log(f"  状态树抽取异常: {type(e).__name__}: {e}")
        return []

    jobs: list[dict] = []
    for r in raw or []:
        sal = _sal_range(r.get("salary") or "")
        # salaryReal 若有更精确的值则优先
        if r.get("salaryReal"):
            alt = _sal_range(r["salaryReal"])
            if alt:
                sal = alt
        desc = clean(r.get("description"))
        degraded = []
        if sal is None:
            degraded.append("薪资未获取")
        if not r.get("companyName"):
            degraded.append("公司名未获取")
        jobs.append({
            "title": clean(r.get("positionName")),
            "salary_raw": r.get("salary"),
            "salary_min": sal[0] if sal else None,
            "salary_max": sal[1] if sal else None,
            "salary_months": _salary_months(r.get("salary") or ""),
            "education": r.get("education"),
            "experience": r.get("experience"),
            "skills": _filter_skills(r.get("labels") or []),
            "welfare": r.get("welfare") or [],
            "company": clean(r.get("companyName")),
            "company_size": clean(r.get("companySize")),
            "company_stage": clean(r.get("companyStage")),
            "industry": clean(r.get("industry")),
            "location": clean(r.get("workAddress")),
            "publish": r.get("publishText"),
            "url": (f"https://www.zhaopin.com/jobdetail/{r['positionName']}"
                    if False else f"https://www.zhaopin.com/jobdetail/CC"
                    f"{r.get('positionId')}.htm"),
            "source": "智联招聘",
            "requirements_text": desc,
            "degraded": degraded,
        })
    return jobs


def _salary_months(salary_text: str) -> int | None:
    """解析「·15薪」这类薪资月数。"""
    m = re.search(r"(\d{1,2})\s*薪", salary_text)
    return int(m.group(1)) if m else None


def extract_zhaopin_dom(page, city: str) -> list[dict]:
    """DOM 兜底解析(状态树不可用时)。"""
    cards_sel = resolve_card_selector(page, SITES["zhaopin"]["card_candidates"])
    if not cards_sel:
        return []
    cards = page.locator(cards_sel)
    out = []
    for i in range(cards.count()):
        rec = parse_zhaopin_card(cards.nth(i))
        if rec:
            out.append(rec)
    return out


def scrape_generic(page, site_key: str, keyword: str, city: str, pages: int,
                   limit: int) -> tuple[list[dict], dict]:
    """需登录站点的通用抓取(尽力而为)。

    这些站点 DOM 会变、且有风控,解析只做通用兜底;取不到的字段一律 null。
    """
    site = SITES[site_key]
    city_code = BOSS_CITY.get(city, "") if site_key == "zhipin" else city
    url = site["search"].format(city_code=city_code, kw=quote(keyword))
    meta = {"site": site["name"], "keyword": keyword, "city": city,
            "search_url": url, "login_wall": False, "salary_samples": [],
            "notes": ["扩展站点为尽力而为通道,DOM 变更即可能失效"]}

    log(f"{site['name']} | {keyword} | {city}")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:  # noqa: BLE001
        meta["notes"].append(f"导航失败: {type(e).__name__}")
        return [], meta
    page.wait_for_timeout(6000)

    if not is_logged_in(page):
        meta["login_wall"] = True
        meta["notes"].append("未登录,需先执行 --login 扫码")
        log("  未登录,跳过")
        return [], meta

    for _ in range(3):
        page.mouse.wheel(0, 1800)
        page.wait_for_timeout(1200)

    jobs: list[dict] = []
    cards_sel = resolve_card_selector(page, site["card_candidates"])
    if not cards_sel:
        meta["notes"].append("未找到卡片,可能改版或需登录")
        return [], meta
    cards = page.locator(cards_sel)
    n = cards.count()
    log(f"  发现 {n} 张卡片")
    for i in range(min(n, limit)):
        try:
            rec = _parse_card_text(cards.nth(i).inner_text(),
                                   source=site["name"], city=city)
            if rec:
                jobs.append(rec)
        except Exception:  # noqa: BLE001
            continue

    meta["salary_samples"] = harvest_page_salaries(page)[:40]
    return jobs, meta


# ---------------------------------------------------------------- 前程无忧

JOB51_EXP_RE = re.compile(r"(\d+)\s*年(?:及以[上下]|以[上下])?")


def _parse_51job_exp(text: str | None) -> str | None:
    """51job 的经验写法是「3年及以上」「5-10年」「经验不限」,归一到统一口径。"""
    if not text:
        return None
    t = str(text).strip()
    if not t or "不限" in t:
        return "经验不限"
    m = re.search(r"(\d+)\s*[-~]\s*(\d+)\s*年", t)
    if m:
        return f"{m.group(1)}-{m.group(2)}年"
    m = re.search(r"(\d+)\s*年及以上", t)
    if m:
        return f"{m.group(1)}年以上"
    m = re.search(r"(\d+)\s*年及以下", t)
    if m:
        return f"{m.group(1)}年以内"
    m = JOB51_EXP_RE.search(t)
    if m:
        return f"{m.group(1)}年"
    return t


def scrape_51job(page, keyword: str, city: str, pages: int, limit: int,
                 scroll_rounds: int) -> tuple[list[dict], dict]:
    """抓取前程无忧。

    数据来源:岗位卡片上的 `sensorsdata` 属性,内含完整结构化 JSON
    (jobId/jobTitle/jobSalary/jobArea/jobYear/jobDegree),无需解析 DOM 文本。
    公司名从卡片文本补(卡片会带「公司名 + 行业 + 规模」)。
    实测免登录可用,列表页薪资不掩码。
    """
    code = JOB51_CITY.get(city, "")
    meta = {"site": "前程无忧", "access": "public", "keyword": keyword, "city": city,
            "login_wall": False, "notes": [], "salary_samples": []}
    jobs: list[dict] = []
    seen: set[str] = set()

    for pno in range(1, pages + 1):
        url = SITES["51job"]["search"].format(city_code=code, kw=quote(keyword), page=pno)
        meta["search_url"] = meta.get("search_url") or url
        log(f"前程无忧 | {keyword} | {city} | 第 {pno}/{pages} 页")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:  # noqa: BLE001
            meta["notes"].append(f"第{pno}页导航失败: {type(e).__name__}")
            continue
        page.wait_for_timeout(5000 + random.randint(0, 1500))
        # 等岗位卡片真正出现(异步渲染,首帧常常是空壳)
        try:
            page.wait_for_selector("[sensorsdata]", timeout=12000)
        except Exception:  # noqa: BLE001
            meta["notes"].append(f"第{pno}页等待卡片超时")
        for _ in range(scroll_rounds):
            page.mouse.wheel(0, 1800)
            page.wait_for_timeout(900 + random.randint(0, 600))

        # 卡片结构化属性(最多重试 3 次,应对渲染延迟)
        raw = []
        for attempt in range(3):
            try:
                raw = page.eval_on_selector_all(
                    "[sensorsdata]", "els => els.map(e => e.getAttribute('sensorsdata'))")
            except Exception:  # noqa: BLE001
                raw = []
            if any(r and "jobTitle" in r for r in (raw or [])):
                break
            log(f"  第 {attempt+1} 次未取到结构化卡片,等待重试")
            page.wait_for_timeout(3000)
            for _ in range(2):
                page.mouse.wheel(0, 1800)
                page.wait_for_timeout(1000)

        # 卡片文本 -> 公司名索引(按顺序与结构化卡片对应)
        try:
            texts = page.eval_on_selector_all(
                ".joblist-item", "els => els.map(e => e.innerText)")
        except Exception:  # noqa: BLE001
            texts = []

        got = 0
        for idx, r in enumerate(raw or []):
            if not r or "jobTitle" not in r:
                continue
            try:
                d = json.loads(r.replace("&quot;", '"'))
            except Exception:  # noqa: BLE001
                continue
            jid = str(d.get("jobId") or "")
            if not jid or jid in seen:
                continue
            seen.add(jid)

            sal = _sal_range(str(d.get("jobSalary") or ""))
            card_text = texts[idx] if idx < len(texts) else ""
            company = _extract_company_from_51job(card_text)
            loc = clean(d.get("jobArea"))

            jobs.append({
                "title": clean(d.get("jobTitle")),
                "salary_raw": clean(d.get("jobSalary")),
                "salary_min": sal[0] if sal else None,
                "salary_max": sal[1] if sal else None,
                "salary_months": _salary_months(str(d.get("jobSalary") or "")),
                "education": clean(d.get("jobDegree")),
                "experience": _parse_51job_exp(d.get("jobYear")),
                "skills": _skills_from_51job_text(card_text),
                "welfare": [],
                "company": company,
                "company_size": _size_from_51job_text(card_text),
                "company_stage": None,
                "industry": None,
                "location": loc,
                "publish": clean(d.get("jobTime")),
                "url": f"https://jobs.51job.com/all/{jid}.html" if jid else None,
                "source": "前程无忧",
                "requirements_text": clean(card_text),
                "degraded": [] if sal else ["薪资未获取"],
            })
            got += 1
            if len(jobs) >= limit:
                break

        log(f"  抽取 {got} 条")
        meta["salary_samples"].extend(harvest_page_salaries(page)[:30])
        if len(jobs) >= limit or got == 0:
            if got == 0:
                meta["notes"].append(f"第{pno}页无结构化卡片")
            break
        time.sleep(2.5 + random.random() * 2)

    return jobs, meta


# 51job 卡片尾部的公司名特征词,用于从文本里定位公司
_COMPANY_SUFFIX = ("有限公司", "股份有限公司", "集团", "公司", "研究院", "事务所", "中心", "厂")
_SIZE_RE = re.compile(r"(少于50人|50-150人|150-500人|500-1000人|1000-5000人|5000-10000人|10000人以上)")


def _extract_company_from_51job(text: str) -> str | None:
    """51job 卡片文本末尾形如「深圳市法本信息技术股份有限公司 计算机软件已上市10000人以上」。

    取最后一个含公司特征词、且长度合理的片段。
    """
    if not text:
        return None
    lines = [x.strip() for x in re.split(r"[\n\r]+", text) if x.strip()]
    for seg in reversed(lines):
        if len(seg) > 60:
            continue
        if any(k in seg for k in _COMPANY_SUFFIX):
            # 去掉后面的行业/规模尾巴
            cut = _SIZE_RE.search(seg)
            if cut:
                seg = seg[:cut.start()]
            seg = re.sub(r"(已上市|未融资|民营|国企|外资|合资|创业公司|事业单位|政府机关)$", "", seg)
            seg = seg.strip()
            if 3 <= len(seg) <= 40:
                return seg
    return None


def _size_from_51job_text(text: str) -> str | None:
    if not text:
        return None
    m = _SIZE_RE.search(text)
    return m.group(1) if m else None


def _skills_from_51job_text(text: str) -> list[str]:
    """51job 的技能多以小写英文短语散在卡片里(如 mysql springboot redis)。"""
    if not text:
        return []
    out, seen = [], set()
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]{1,18}", text):
        tl = tok.lower()
        if tl in ("nan",) or tl in seen:
            continue
        seen.add(tl)
        out.append(tok)
    return _filter_skills(out)[:20]


# ---------------------------------------------------------------- 官方源


def scrape_mohrss(page, keyword: str, city: str, pages: int, limit: int,
                  scroll_rounds: int) -> tuple[list[dict], dict]:
    """抓取中国公共招聘网(人社部官方)。

    仅提供:岗位名称 / 单位名称 / 月薪档 / 地区。**没有完整 JD、学历、经验字段**,
    因此只作为「官方口径 + 岗位总量」的补充源,标 structured=False。
    """
    meta = {"site": "中国公共招聘网(人社部)", "access": "public", "keyword": keyword,
            "city": city, "login_wall": False, "structured": False,
            "notes": ["官方源:仅岗位名/单位/薪资档/地区,无 JD 与学历经验"], "salary_samples": []}
    jobs: list[dict] = []
    seen: set[str] = set()

    for pno in range(1, pages + 1):
        url = SITES["mohrss"]["search"].format(page=pno)
        meta["search_url"] = meta.get("search_url") or url
        log(f"公共招聘网 | 第 {pno}/{pages} 页")
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:  # noqa: BLE001
            meta["notes"].append(f"第{pno}页导航失败: {type(e).__name__}")
            continue
        page.wait_for_timeout(3500)

        rows = []
        try:
            rows = page.eval_on_selector_all(
                "table tr, .list li, [class*=list] li",
                """els => els.map(e => (e.innerText||'').replace(/\\s+/g,' ').trim())
                        .filter(t => t && t.length > 8)""")
        except Exception:  # noqa: BLE001
            pass

        got = 0
        for t in rows:
            # 形态:「岗位名 单位名 5000元以上 地区」
            m = re.match(r"^(.{2,40}?)\s+(.{2,60}?)\s+(\d[\d\-,.万千元以上]*)\s+(\S{2,12})$", t)
            if not m:
                continue
            title, company, sal_txt, area = m.groups()
            if any(k in title for k in ("岗位名称", "月薪范围")):
                continue
            if city and city not in area and city != "全国":
                continue
            key = f"{title}|{company}|{area}"
            if key in seen:
                continue
            seen.add(key)
            sal = _sal_range(sal_txt.replace("以上", "").replace("元", "元"))
            jobs.append({
                "title": clean(title),
                "salary_raw": clean(sal_txt),
                "salary_min": sal[0] if sal else None,
                "salary_max": sal[1] if sal else None,
                "salary_months": None,
                "education": None,
                "experience": None,
                "skills": [],
                "welfare": [],
                "company": clean(company),
                "company_size": None,
                "company_stage": None,
                "industry": None,
                "location": clean(area),
                "publish": None,
                "url": None,
                "source": "中国公共招聘网(人社部)",
                "requirements_text": None,
                "degraded": ["官方源:无 JD/学历/经验字段", "薪资为粗档"],
            })
            got += 1
            if len(jobs) >= limit:
                break
        log(f"  抽取 {got} 条")
        if len(jobs) >= limit or got == 0:
            break
        time.sleep(2 + random.random() * 2)

    return jobs, meta


# ---------------------------------------------------------------- 入口


def run_login(chrome: str, profile: str, site_key: str) -> None:
    site = SITES[site_key]
    url = {"zhipin": "https://www.zhipin.com/web/user/",
           "liepin": "https://www.liepin.com/",
           "51job": "https://login.51job.com/"}.get(site_key, site["search"].format(kw="", city_code=""))
    log(f"打开 {site['name']} 登录页(有头浏览器)。请在弹出的窗口里扫码登录,")
    log("登录成功后本窗口保持 3 分钟再自动关闭,期间不要关闭浏览器。")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            profile, executable_path=chrome, headless=False, locale="zh-CN",
            user_agent=UA, viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:  # noqa: BLE001
            log(f"导航异常(可忽略): {type(e).__name__}")
        for _ in range(36):  # 约 3 分钟
            page.wait_for_timeout(5000)
        ctx.close()
    log(f"登录态已保存到 {profile}。后续对 {site['name']} 的抓取将复用该状态(有效期取决于站点)。")


def build_source_status(sites: list[str], all_meta: list[dict]) -> list[dict]:
    """汇总各数据源的可用状态,供上层如实告知用户「哪些源拿到了数据、哪些要登录」。"""
    by_site: dict[str, dict] = {}
    for m in all_meta:
        name = m.get("site")
        if not name:
            continue
        rec = by_site.setdefault(name, {"site": name, "jobs": 0, "login_wall": False,
                                        "notes": [], "access": m.get("access", "public")})
        rec["jobs"] += m.get("_count", 0)
        rec["login_wall"] = rec["login_wall"] or bool(m.get("login_wall"))
        for n in m.get("notes") or []:
            if n not in rec["notes"]:
                rec["notes"].append(n)

    status: list[dict] = []
    for key in sites:
        site = SITES[key]
        rec = by_site.get(site["name"])
        entry = {
            "key": key, "site": site["name"], "access": site["access"],
            "structured": site.get("structured", False),
            "jobs": rec["jobs"] if rec else 0,
            "ok": bool(rec and rec["jobs"] > 0),
            "notes": rec["notes"] if rec else [],
        }
        if site["needs_login"] and not entry["ok"]:
            entry["advice"] = f"{site['name']}需要登录后才能获取数据,可执行 --login --site {key} 扫码"
        elif not entry["ok"]:
            entry["advice"] = f"{site['name']}本次未取到数据(可能改版或限流),稍后可重试"
        status.append(entry)
    return status


def main() -> None:
    ap = argparse.ArgumentParser(description="招聘信息采集器")
    ap.add_argument("--keyword", "-k", action="append", default=[],
                    help="职位关键词,可多次传入")
    ap.add_argument("--city", "-c", default="全国", help="城市名,如 北京 / 深圳")
    ap.add_argument("--site", "-s", action="append", default=None,
                    help="站点:zhaopin/51job/mohrss/zhipin,默认 zhaopin+51job")
    ap.add_argument("--pages", type=int, default=3, help="每个关键词翻页数")
    ap.add_argument("--limit", type=int, default=60, help="每个关键词最多采集条数")
    ap.add_argument("--scroll-rounds", type=int, default=4, help="每页滚动轮数")
    ap.add_argument("--out", "-o", default="./out", help="输出目录")
    ap.add_argument("--chrome", default=None, help="chromium 可执行文件路径")
    ap.add_argument("--profile", default=None, help="登录态持久化目录")
    ap.add_argument("--refresh", action="store_true", help="忽略已有 raw 结果强制重抓")
    ap.add_argument("--login", action="store_true", help="进入扫码登录模式")
    ap.add_argument("--check-sources", action="store_true",
                    help="只检测各数据源可用性,不采集")
    ap.add_argument("--headless", default="1", help="1=无头(默认) 0=有头")
    args = ap.parse_args()

    # --check-sources 是纯静态自检:既不需要浏览器,也不需要 playwright。
    # 必须放在 detect_chrome() 之前 —— 后者在找不到 chromium 时会 SystemExit(1),
    # 那会让本命令在任何未装浏览器的环境(CI runner 等)恒定失败。
    if args.check_sources:
        log("数据源可用性(静态声明,基于实测结论):")
        for key, site in SITES.items():
            tag = "免登录" if site["access"] == "public" else "需登录"
            struct = "完整JD" if site.get("structured") else "字段有限"
            log(f"  {site['name']:22} [{tag}] [{struct}]")
        log("实测不可用的源:")
        for key, info in UNAVAILABLE_SOURCES.items():
            log(f"  {info['name']:22} 原因: {info['reason']}")
        return

    chrome = detect_chrome(args.chrome)

    # 依赖检查必须早于建目录:否则在缺 playwright 的环境里
    # 一次失败的抓取尝试也会留下空的 out/ 与 raw/
    if sync_playwright is None:
        raise SystemExit(
            "缺少 playwright 依赖。请先安装:\n"
            "  pip install playwright\n"
            "（仅 --check-sources 与 --help 不需要该依赖）")

    out_dir = Path(args.out)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    profile = args.profile or str(out_dir / "profile")

    if args.login:
        run_login(chrome, profile, (args.site or ["zhipin"])[0])
        return

    if not args.keyword:
        raise SystemExit("未提供关键词。示例:--keyword \"Java开发\" --city 北京")

    # 默认:两个免登录的结构化源(智联 + 前程无忧),互为冗余
    sites = args.site or ["zhaopin", "51job"]
    unknown = [s for s in sites if s not in SITES]
    if unknown:
        raise SystemExit(f"未知站点 {unknown},可选:{list(SITES)}")

    all_jobs: list[dict] = []
    all_meta: list[dict] = []

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            profile, executable_path=chrome,
            headless=(args.headless != "0"), locale="zh-CN",
            user_agent=UA, viewport={"width": 1440, "height": 900},
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        for site_key in sites:
            for kw in args.keyword:
                raw_file = raw_dir / f"{site_key}__{slugify(kw)}__{slugify(args.city)}.json"
                if raw_file.exists() and not args.refresh:
                    try:
                        cached = json.loads(raw_file.read_text(encoding="utf-8"))
                        if cached.get("jobs"):
                            log(f"复用已有结果 {raw_file.name} ({len(cached['jobs'])} 条)")
                            all_jobs.extend(cached["jobs"])
                            cm = cached.get("meta", {})
                            cm["_count"] = len(cached["jobs"])
                            all_meta.append(cm)
                            continue
                    except Exception:  # noqa: BLE001
                        pass

                page = ctx.new_page()
                try:
                    if site_key == "zhaopin":
                        jobs, meta = scrape_zhaopin(
                            page, kw, args.city, args.pages, args.limit, args.scroll_rounds)
                    elif site_key == "51job":
                        jobs, meta = scrape_51job(
                            page, kw, args.city, args.pages, args.limit, args.scroll_rounds)
                    elif site_key == "mohrss":
                        jobs, meta = scrape_mohrss(
                            page, kw, args.city, args.pages, args.limit, args.scroll_rounds)
                    else:
                        jobs, meta = scrape_generic(
                            page, site_key, kw, args.city, args.pages, args.limit)
                finally:
                    page.close()

                meta["fetched_at"] = datetime.now().isoformat(timespec="seconds")
                meta["_count"] = len(jobs)
                raw_file.write_text(
                    json.dumps({"meta": meta, "jobs": jobs}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
                all_jobs.extend(jobs)
                all_meta.append(meta)
                log(f"  写入 {raw_file.name} ({len(jobs)} 条)")
                time.sleep(3 + random.random() * 3)  # 站间节流
        ctx.close()

    source_status = build_source_status(sites, all_meta)

    # 去重:同一岗位可能被多个源收录
    deduped, seen = [], set()
    for j in all_jobs:
        key = f"{(j.get('title') or '').strip()}|{(j.get('company') or '').strip()}"
        if key in seen and j.get("company"):
            continue
        seen.add(key)
        deduped.append(j)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "query": {"keywords": args.keyword, "city": args.city, "sites": sites},
        "meta": all_meta,
        "source_status": source_status,
        "count_raw": len(all_jobs),
        "count": len(deduped),
        "jobs": deduped,
    }
    (out_dir / "jobs.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"完成:原始 {len(all_jobs)} 条,去重后 {len(deduped)} 条 -> {out_dir / 'jobs.json'}")

    # 明确报告各源状态
    log("各数据源状态:")
    for s in source_status:
        mark = "OK" if s["ok"] else "无数据"
        log(f"  [{mark}] {s['site']} ({'免登录' if s['access']=='public' else '需登录'}) "
            f"{s['jobs']} 条" + (f" — {s['advice']}" if s.get("advice") else ""))

    if not all_jobs:
        log("未取到任何岗位。可能原因:站点改版、限流、或需要登录。")
        log("可尝试:--headless 0 观察页面 / --login 先扫码 / 稍后重试。")


if __name__ == "__main__":
    main()
