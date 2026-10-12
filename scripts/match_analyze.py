#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""岗位匹配分析器 — 输入 jobs.json + 求职者画像,输出匹配打分与合理化建议。

用法:
    python match_analyze.py --jobs ./out/jobs.json --profile ./out/profile.json --out ./out
    python match_analyze.py --jobs ./out/jobs.json --name 张三 --degree 本科 \
        --experience 3 --city 北京 --expect 18000 --skills Java,Spring,MySQL --out ./out

产出:
    out/analysis.json   结构化分析结果(供报告生成使用)

核心原则:
    - 所有评分可解释:每个分数都能追溯到具体依据
    - 缺失数据不臆造:学历/薪资缺失时降低该项权重并标注,而非假设
    - 薪资校准基于**本次抓取的真实样本**做分位,不引用外部不可验证的市场均值
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- 口径归一

DEGREE_RANK = {
    "不限": 0, "学历不限": 0, "初中": 1, "高中": 1, "中专": 1, "中技": 1,
    "大专": 2, "专科": 2, "本科": 3, "学士": 3, "硕士": 4, "研究生": 4,
    "MBA": 4, "博士": 5,
}
DEGREE_LABEL = {0: "学历不限", 1: "高中及以下", 2: "大专", 3: "本科", 4: "硕士", 5: "博士"}

# 从工作地址里识别城市(长名在前,避免「深圳」先于「深圳」以外的误切)
CITY_NAMES = [
    "石家庄", "呼和浩特", "乌鲁木齐", "哈尔滨", "齐齐哈尔",
    "北京", "上海", "广州", "深圳", "杭州", "成都", "南京", "武汉", "西安",
    "苏州", "天津", "重庆", "长沙", "郑州", "青岛", "合肥", "宁波", "东莞",
    "佛山", "济南", "福州", "厦门", "大连", "沈阳", "昆明", "无锡", "南昌",
    "贵阳", "太原", "兰州", "南宁", "海口", "长春", "温州", "珠海", "中山",
    "惠州", "泉州", "南通", "徐州", "常州", "嘉兴", "绍兴", "台州", "金华",
]


def norm_degree(text: str | None) -> int | None:
    if not text:
        return None
    for k, v in DEGREE_RANK.items():
        if k in text:
            return v
    return None


def parse_exp_years(text: str | None) -> tuple[float, float] | None:
    """把经验要求解析成 (下限, 上限) 年。返回 (lo, hi),上限用 1e9 表示不限。"""
    if not text:
        return None
    t = text.strip()
    if "不限" in t or "无经验" in t or "在校" in t or "实习" in t:
        return (0.0, 1e9)
    if "应届" in t:
        return (0.0, 1.0)
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-~至]\s*(\d+(?:\.\d+)?)", t)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    m = re.search(r"(\d+(?:\.\d+)?)\s*年以[上下内]", t)
    if m:
        v = float(m.group(1))
        if "以上" in t:
            return (v, 1e9)
        return (0.0, v)
    m = re.search(r"(\d{2})\s*年以上", t)   # 「10年以上」
    if m:
        return (float(m.group(1)), 1e9)
    m = re.search(r"(\d+(?:\.\d+)?)\s*年", t)
    if m:
        v = float(m.group(1))
        return (v, v)
    return None


def fmt_exp(pair: tuple[float, float] | None) -> str:
    if not pair:
        return "未标注"
    lo, hi = pair
    if lo == 0 and hi >= 1e9:
        return "经验不限"
    if hi >= 1e9:
        return f"{lo:g}年以上"
    if lo == hi:
        return f"{lo:g}年"
    return f"{lo:g}-{hi:g}年"


def fmt_salary(v: float | None) -> str:
    if not v:
        return "—"
    if v >= 10000:
        return f"{v/10000:.1f}万"
    return f"{int(v)}元"


def percentile(sorted_vals: list[float], p: float) -> float | None:
    """线性插值分位数。"""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    f, c = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


# ---------------------------------------------------------------- 评分


def score_salary(job: dict, expect: float | None, market: dict) -> dict:
    """薪资匹配度:期望 vs 岗位区间 + 市场分位定位。"""
    smin, smax = job.get("salary_min"), job.get("salary_max")
    if not smin or not smax:
        return {"score": None, "note": "岗位未标注薪资,不参与薪资评分", "level": "未知"}
    mid = (smin + smax) / 2
    if not expect:
        return {"score": 60, "note": f"岗位月薪中值 {fmt_salary(mid)},未提供期望薪资,按中位参照",
                "level": "待定", "job_mid": mid}

    if expect < smin:
        # 期望低于岗位下限,说明岗位更高级别或期望偏低
        ratio = expect / smin
        score = 75 if ratio > 0.7 else 62
        note = f"期望 {fmt_salary(expect)} 低于岗位下限 {fmt_salary(smin)},可尝试争取更高"
        level = "偏低"
    elif expect > smax:
        ratio = expect / smax
        if ratio <= 1.15:
            score, level = 70, "略高"
            note = f"期望 {fmt_salary(expect)} 略高于上限 {fmt_salary(smax)},可谈"
        elif ratio <= 1.4:
            score, level = 45, "偏高"
            note = f"期望 {fmt_salary(expect)} 明显高于上限 {fmt_salary(smax)},命中难度较大"
        else:
            score, level = 22, "过高"
            note = f"期望 {fmt_salary(expect)} 远超上限 {fmt_salary(smax)},基本不匹配"
    else:
        # 落在区间内:越靠近中上越优
        pos = (expect - smin) / max(smax - smin, 1)
        score = 88 + round(pos * 8)
        score = min(score, 96)
        level = "匹配"
        note = f"期望 {fmt_salary(expect)} 落在区间 {fmt_salary(smin)}-{fmt_salary(smax)} 内"

    # 市场分位定位
    p50 = market.get("p50")
    if p50:
        note += f";本地同岗位样本中位 {fmt_salary(p50)}"
    return {"score": score, "note": note, "level": level, "job_mid": mid}


def score_degree(job: dict, deg: int | None) -> dict:
    need = norm_degree(job.get("education"))
    if need is None:
        return {"score": None, "note": "岗位未标注学历,不参与评分", "level": "未知"}
    if deg is None:
        return {"score": None, "note": f"岗位要求{DEGREE_LABEL.get(need)};未提供学历,无法比对",
                "level": "待定", "need": need}
    gap = deg - need
    if gap >= 0:
        return {"score": 95, "note": f"学历满足(岗位要求{DEGREE_LABEL.get(need)})", "level": "满足",
                "need": need}
    if gap == -1:
        return {"score": 55, "note": f"学历差一档(岗位要求{DEGREE_LABEL.get(need)}),部分公司可放宽",
                "level": "临界", "need": need}
    return {"score": 25, "note": f"学历不满足(岗位要求{DEGREE_LABEL.get(need)}),通常会被筛掉",
            "level": "不满足", "need": need}


def score_experience(job: dict, years: float | None) -> dict:
    pair = parse_exp_years(job.get("experience"))
    if not pair:
        return {"score": None, "note": "岗位未标注经验要求,不参与评分", "level": "未知"}
    lo, hi = pair
    if years is None:
        return {"score": None, "note": f"岗位要求 {fmt_exp(pair)};未提供经验年限,无法比对",
                "level": "待定", "need": pair}
    if lo <= years <= hi:
        return {"score": 92, "note": f"经验匹配(要求 {fmt_exp(pair)},你 {years:g} 年)",
                "level": "匹配", "need": pair}
    if years < lo:
        d = lo - years
        if d <= 1:
            return {"score": 62, "note": f"经验略欠(要求 {fmt_exp(pair)},你 {years:g} 年),可投",
                    "level": "临界", "need": pair}
        return {"score": 30, "note": f"经验不足(要求 {fmt_exp(pair)},你 {years:g} 年)",
                "level": "不足", "need": pair}
    # 超出上限
    return {"score": 72, "note": f"经验高于要求(要求 {fmt_exp(pair)},你 {years:g} 年),注意岗位是否偏初级",
            "level": "高于要求", "need": pair}


def score_skills(job: dict, skills: list[str]) -> dict:
    tags = [s for s in (job.get("skills") or []) if s]
    if not tags:
        return {"score": None, "note": "岗位未列出技能标签,不参与评分",
                "level": "未知", "hit": [], "miss": []}
    if not skills:
        return {"score": None, "note": f"岗位列出:{'、'.join(tags[:8])};未提供你的技能,无法比对",
                "level": "待定", "hit": [], "miss": tags}

    mine = []
    for s in skills:
        s = (s or "").strip()
        if s:
            mine.append(s)
            # 中文技能常写法不同(「项目管理」vs「项目管理能力」),
            # 同时登记去后缀的短形,提高中文命中率
            if len(s) > 3 and s.endswith(("能力", "经验", "技能")):
                mine.append(s[:-2])
    mine_l = [m.lower() for m in mine]

    def matched(tag: str) -> bool:
        tl = tag.lower()
        for m in mine_l:
            if not m:
                continue
            if tl == m:
                return True
            # 子串包含:任一侧长度 >= 2 才做,避免单字误命中
            if len(m) >= 2 and (m in tl or tl in m):
                return True
            # 中文同义:去掉常见修饰后比对(如「数据分析」vs「数据分析师」)
            if re.sub(r"(师|员|能力|经验|技能)$", "", tl) == \
               re.sub(r"(师|员|能力|经验|技能)$", "", m):
                return True
        return False

    hit, miss = [], []
    for t in tags:
        (hit if matched(t) else miss).append(t)

    ratio = len(hit) / len(tags)
    score = round(35 + ratio * 60)
    if ratio >= 0.7:
        level = "覆盖良好"
    elif ratio >= 0.4:
        level = "部分覆盖"
    else:
        level = "缺口较大"
    note = f"技能命中 {len(hit)}/{len(tags)}"
    if miss:
        note += f";缺: {'、'.join(miss[:8])}"
    return {"score": score, "note": note, "level": level, "hit": hit, "miss": miss}


WEIGHTS = {"salary": 0.30, "degree": 0.20, "experience": 0.22, "skills": 0.28}


def score_job(job: dict, profile: dict, market: dict) -> dict:
    parts = {
        "salary": score_salary(job, profile.get("expect_salary"), market),
        "degree": score_degree(job, profile.get("degree_rank")),
        "experience": score_experience(job, profile.get("experience_years")),
        "skills": score_skills(job, profile.get("skills") or []),
    }
    num, den = 0.0, 0.0
    for key, w in WEIGHTS.items():
        s = parts[key]["score"]
        if s is None:
            continue
        num += s * w
        den += w
    total = round(num / den) if den else None
    return {"total": total, "parts": parts,
            "scored_dims": sorted(k for k, v in parts.items() if v["score"] is not None)}


def verdict(score: int | None) -> tuple[str, str]:
    if score is None:
        return "信息不足", "数据缺失,建议补充岗位信息"
    if score >= 80:
        return "强匹配", "建议优先投递"
    if score >= 65:
        return "较匹配", "值得投递,注意补短板"
    if score >= 50:
        return "一般", "可投,但需评估投入产出"
    return "弱匹配", "不建议优先投入"


# ---------------------------------------------------------------- 建议生成


def build_salary_advice(profile: dict, market: dict, scored: list[dict]) -> dict:
    expect = profile.get("expect_salary")
    p25, p50, p75, p90 = market.get("p25"), market.get("p50"), market.get("p75"), market.get("p90")
    advice = {"market": market, "expect": expect, "verdict": None, "range": None, "text": []}

    if not market.get("n"):
        advice["text"].append("本次样本薪资数据不足,无法做市场校准。建议扩大抓取页数或换更主流的职位关键词。")
        return advice

    if expect and p25 and p75:
        lo, hi = round(p25), round(p75)
        advice["range"] = [lo, hi]
        if expect < p25:
            advice["verdict"] = "偏低"
            advice["text"].append(
                f"你的期望 {fmt_salary(expect)} 低于本地市场 25 分位 {fmt_salary(p25)}。"
                f"以你的目标岗位看,合理区间约 {fmt_salary(lo)}–{fmt_salary(hi)},"
                f"建议上调期望,否则容易被砍价或误判为经验不足。")
        elif expect <= p50:
            advice["verdict"] = "合理偏保守"
            advice["text"].append(
                f"你的期望 {fmt_salary(expect)} 在市场 25–50 分位之间({fmt_salary(p25)}–{fmt_salary(p50)}),"
                f"属于稳妥开价。若履历有亮点(项目、证书、大厂背景),可试探 {fmt_salary(hi)} 附近。")
        elif expect <= p75:
            advice["verdict"] = "合理"
            advice["text"].append(
                f"你的期望 {fmt_salary(expect)} 落在市场 50–75 分位({fmt_salary(p50)}–{fmt_salary(hi)}),"
                f"与市场主流水平一致,属于合理区间。")
        else:
            advice["verdict"] = "偏高"
            tail = market.get("p90")
            advice["text"].append(
                f"你的期望 {fmt_salary(expect)} 高于本地市场 75 分位 {fmt_salary(p75)}"
                + (f"(90 分位约 {fmt_salary(tail)})" if tail else "")
                + "。达标岗位集中在头部公司或更高职级,建议:①突出可量化的成果;"
                  "②同时按区间下沿投递一批以保底;③若长期无回音,回调至 75 分位附近。")
    elif expect:
        advice["text"].append(f"你填写了期望 {fmt_salary(expect)},但本次样本薪资字段不足,无法定位分位。")

    # 补充:本次样本中达到期望的岗位占比
    if expect:
        hit = [j for j in scored
               if (j["job"].get("salary_max") or 0) >= expect]
        ratio = len(hit) / max(len(scored), 1)
        advice["reachable_ratio"] = round(ratio, 3)
        advice["text"].append(
            f"本次采集的 {len(scored)} 个岗位中,有 {len(hit)} 个({ratio*100:.0f}%)"
            f"的薪资上限达到或超过你的期望 {fmt_salary(expect)}。")
    return advice


def build_gap_advice(scored: list[dict]) -> dict:
    """汇总能力差距:统计高频缺口与高频命中。"""
    miss_counter: dict[str, int] = {}
    hit_counter: dict[str, int] = {}
    deg_block = 0
    exp_block = 0
    total = len(scored)

    for item in scored:
        parts = item["analysis"]["parts"]
        for m in parts["skills"].get("miss") or []:
            miss_counter[m] = miss_counter.get(m, 0) + 1
        for h in parts["skills"].get("hit") or []:
            hit_counter[h] = hit_counter.get(h, 0) + 1
        if parts["degree"].get("level") in ("不满足", "临界"):
            deg_block += 1
        if parts["experience"].get("level") in ("不足", "临界"):
            exp_block += 1

    top_miss = sorted(miss_counter.items(), key=lambda x: -x[1])[:12]
    top_hit = sorted(hit_counter.items(), key=lambda x: -x[1])[:10]

    advice = {"top_missing_skills": top_miss, "top_matched_skills": top_hit,
              "degree_block_ratio": round(deg_block / total, 3) if total else None,
              "experience_block_ratio": round(exp_block / total, 3) if total else None,
              "text": []}

    if top_miss:
        names = "、".join(f"{k}({v}个岗位)" for k, v in top_miss[:6])
        advice["text"].append(f"最常被要求但你尚未掌握的技能:{names}。")
        advice["text"].append(
            f"建议优先级:先补出现频次最高、且与你现有经验最接近的 1–2 项。"
            f"补齐方式按岗位性质选择——技术岗做可展示的作品(仓库/demo),"
            f"工程与管理岗则靠项目经历口径统一、参与投标/结算等实战经手记录来体现。")
    else:
        advice["text"].append("在本次样本的技能标签层面没有发现明显缺口,可把精力放在项目深度与面试表达上。")

    if advice["degree_block_ratio"] and advice["degree_block_ratio"] > 0.3:
        advice["text"].append(
            f"注意:{advice['degree_block_ratio']*100:.0f}% 的岗位学历要求高于你的学历。"
            f"建议优先投递标注「学历不限/大专」的岗位,或选择明确看重经验的行业(如部分外包、中小企业)。")
    if advice["experience_block_ratio"] and advice["experience_block_ratio"] > 0.3:
        advice["text"].append(
            f"注意:{advice['experience_block_ratio']*100:.0f}% 的岗位经验要求高于你的年限。"
            f"建议把项目经历按「解决什么问题—怎么做—结果量化」重写,用成果弥补年限。")
    return advice


def build_apply_strategy(scored: list[dict], profile: dict) -> dict:
    """投递策略:分层推荐 + 关键词优化。"""
    ranked = sorted(scored, key=lambda x: (x["analysis"]["total"] or -1), reverse=True)
    strong = [x for x in ranked if (x["analysis"]["total"] or 0) >= 80]
    good = [x for x in ranked if 65 <= (x["analysis"]["total"] or 0) < 80]
    normal = [x for x in ranked if 50 <= (x["analysis"]["total"] or 0) < 65]

    # 公司规模分布(反映岗位层次)
    size_counter: dict[str, int] = {}
    for x in scored:
        sz = x["job"].get("company_size")
        if sz:
            key = re.sub(r"[（(].*?[)）]", "", sz).strip()
            size_counter[key] = size_counter.get(key, 0) + 1

    # 关键词:从高频技能标签 + 岗位名高频词提炼
    kw_counter: dict[str, int] = {}
    for x in scored:
        for s in x["job"].get("skills") or []:
            kw_counter[s] = kw_counter.get(s, 0) + 1
    top_kw = [k for k, _ in sorted(kw_counter.items(), key=lambda i: -i[1])[:12]]

    advice = {
        "tiers": {
            "强匹配": [x["job"].get("title") for x in strong][:10],
            "较匹配": [x["job"].get("title") for x in good][:10],
            "一般": [x["job"].get("title") for x in normal][:10],
        },
        "counts": {"强匹配": len(strong), "较匹配": len(good), "一般": len(normal),
                   "总数": len(scored)},
        "company_size_distribution": size_counter,
        "resume_keywords": top_kw,
        "text": [],
    }

    advice["text"].append(
        f"本次共评估 {len(scored)} 个岗位:强匹配 {len(strong)} 个、较匹配 {len(good)} 个、"
        f"一般 {len(normal)} 个。建议按 强匹配:较匹配:一般 = 5:3:2 的比例投递,"
        f"不要只投最匹配的,也不要海投低匹配岗位。")
    if top_kw:
        advice["text"].append(
            f"简历关键词建议覆盖:{'、'.join(top_kw[:8])}。"
            f"招聘系统的初筛多为关键词匹配,请把这些词自然写进「技能」与「项目描述」两处。")
    if advice["counts"]["强匹配"] == 0 and len(scored) >= 5:
        advice["text"].append(
            "当前没有强匹配岗位,三种调整方向:①放宽城市或岗位关键词;②下调期望薪资一档;"
            "③按上面的技能缺口先补 1 项再投,转化率会明显提升。")
    return advice


def norm_city(loc: str | None) -> str | None:
    """从工作地址中提取规范城市名。

    智联 workAddress 形如「深圳龙岗区坂田」「北京海淀区中关村」,
    直接截前 3 字会得到「深圳龙」这类垃圾值,必须先按城市名匹配。
    """
    if not loc:
        return None
    loc = loc.strip()
    for city in CITY_NAMES:
        if city in loc:
            return city
    # 直辖市/特别行政区之外的省(极少出现),取前 2–4 字作为区域名
    m = re.match(r"^([\u4e00-\u9fff]{2,4}?)(?:市|省|区|县)", loc)
    if m:
        return m.group(1)
    return loc[:4] if len(loc) >= 2 else loc


def build_city_comparison(jobs: list[dict]) -> dict:
    """按岗位所在城市汇总薪资与需求量。"""
    buckets: dict[str, list[float]] = {}
    for j in jobs:
        if not j.get("salary_min"):
            continue
        city = norm_city(j.get("location"))
        if not city:
            continue
        # 统一用区间中值,与整体市场口径保持一致
        mid = ((j["salary_min"] + j["salary_max"]) / 2
               if j.get("salary_max") else j["salary_min"])
        buckets.setdefault(city, []).append(mid)
    rows = []
    for city, vals in buckets.items():
        vals.sort()
        rows.append({
            "city": city, "count": len(vals),
            "p25": round(percentile(vals, 0.25) or 0),
            "p50": round(percentile(vals, 0.5) or 0),
            "p75": round(percentile(vals, 0.75) or 0),
        })
    rows.sort(key=lambda r: (-r["count"], -(r["p50"] or 0)))
    return {"rows": rows[:15],
            "scale": "区间中值",
            "note": "基于本次采集样本统计,仅反映当前抓取范围,不代表全市场。"}


# ---------------------------------------------------------------- 主流程


def analyze(jobs_payload: dict, profile: dict) -> dict:
    jobs = [j for j in jobs_payload.get("jobs", []) if j.get("title")]

    # 市场基准(来自本次真实样本)
    # 全部分位统一建立在「区间中值」序列上。早期版本 p25/p75 取下限序列、
    # p50 取中值序列,两个尺度混用会导致 p75 < p50 的倒挂,已修正。
    all_mins = sorted(j["salary_min"] for j in jobs if j.get("salary_min"))
    mids = sorted((j["salary_min"] + j["salary_max"]) / 2
                  for j in jobs if j.get("salary_min") and j.get("salary_max"))
    base = mids or all_mins          # 有中值用中值,否则退回下限
    market = {
        "n": len(all_mins),
        "scale": "区间中值" if mids else "区间下限",
        "p25": percentile(base, 0.25), "p50": percentile(base, 0.5),
        "p75": percentile(base, 0.75), "p90": percentile(base, 0.90),
        "min": base[0] if base else None,
        "max": base[-1] if base else None,
        "mid_mean": round(statistics.mean(mids)) if mids else None,
    }

    scored = []
    for j in jobs:
        ana = score_job(j, profile, market)
        scored.append({"job": j, "analysis": ana})

    scored.sort(key=lambda x: (x["analysis"]["total"] or -1), reverse=True)

    dist = {"强匹配": 0, "较匹配": 0, "一般": 0, "弱匹配": 0, "信息不足": 0}
    for x in scored:
        v, _ = verdict(x["analysis"]["total"])
        dist[v] = dist.get(v, 0) + 1

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "query": jobs_payload.get("query", {}),
        "profile": profile,
        "sample": {
            "total_jobs": len(jobs),
            "with_salary": len(all_mins),
            "salary_coverage": round(len(all_mins) / len(jobs), 3) if jobs else 0,
            "sources": sorted({j.get("source") for j in jobs if j.get("source")}),
        },
        "market": market,
        "distribution": dist,
        "jobs": [
            {
                "title": x["job"].get("title"),
                "company": x["job"].get("company"),
                "company_size": x["job"].get("company_size"),
                "industry": x["job"].get("industry"),
                "location": x["job"].get("location"),
                "salary_raw": x["job"].get("salary_raw"),
                "salary_min": x["job"].get("salary_min"),
                "salary_max": x["job"].get("salary_max"),
                "education": x["job"].get("education"),
                "experience": x["job"].get("experience"),
                "skills": x["job"].get("skills"),
                "welfare": x["job"].get("welfare"),
                "url": x["job"].get("url"),
                "source": x["job"].get("source"),
                "requirements_text": x["job"].get("requirements_text"),
                "degraded": x["job"].get("degraded"),
                "score": x["analysis"]["total"],
                "verdict": verdict(x["analysis"]["total"])[0],
                "action": verdict(x["analysis"]["total"])[1],
                "parts": x["analysis"]["parts"],
            }
            for x in scored
        ],
    }
    result["advice"] = {
        "salary": build_salary_advice(profile, market, scored),
        "gaps": build_gap_advice(scored),
        "apply": build_apply_strategy(scored, profile),
        "cities": build_city_comparison(jobs),
    }
    return result


def load_profile(args) -> dict:
    if args.profile:
        data = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    else:
        data = {}
    # 命令行覆盖文件值
    if args.name:
        data["name"] = args.name
    if args.degree:
        data["degree"] = args.degree
    if args.experience is not None:
        data["experience_years"] = args.experience
    if args.city:
        data["city"] = args.city
    if args.expect:
        data["expect_salary"] = args.expect
    if args.skills:
        data["skills"] = [s.strip() for s in args.skills.split(",") if s.strip()]

    data.setdefault("name", "求职者")
    data.setdefault("skills", [])
    data["degree_rank"] = norm_degree(data.get("degree"))
    if data.get("experience_years") is not None:
        try:
            data["experience_years"] = float(data["experience_years"])
        except (TypeError, ValueError):
            data["experience_years"] = None
    if data.get("expect_salary") is not None:
        try:
            data["expect_salary"] = float(data["expect_salary"])
        except (TypeError, ValueError):
            data["expect_salary"] = None
    return data


def main() -> None:
    ap = argparse.ArgumentParser(description="岗位匹配分析器")
    ap.add_argument("--jobs", "-j", required=True, help="jobs.json 路径")
    ap.add_argument("--profile", "-p", default=None, help="求职者画像 JSON 路径")
    ap.add_argument("--out", "-o", default=None, help="输出目录(默认与 jobs.json 同目录)")
    ap.add_argument("--name", default=None)
    ap.add_argument("--degree", default=None, help="最高学历,如 本科")
    ap.add_argument("--experience", type=float, default=None, help="工作年限,如 3")
    ap.add_argument("--city", default=None)
    ap.add_argument("--expect", type=float, default=None, help="期望月薪(元),如 18000")
    ap.add_argument("--skills", default=None, help="逗号分隔技能,如 Java,Spring,MySQL")
    args = ap.parse_args()

    jobs_path = Path(args.jobs)
    if not jobs_path.exists():
        raise SystemExit(f"找不到 {jobs_path},请先运行 scrape_jobs.py")
    payload = json.loads(jobs_path.read_text(encoding="utf-8"))
    if not payload.get("jobs"):
        raise SystemExit("jobs.json 中没有岗位数据,无法分析。请先确认抓取成功。")

    profile = load_profile(args)
    result = analyze(payload, profile)

    out_dir = Path(args.out) if args.out else jobs_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "profile.json").write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "analysis.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"样本 {result['sample']['total_jobs']} 个岗位,"
          f"薪资覆盖 {result['sample']['salary_coverage']*100:.0f}%")
    print(f"分档: {result['distribution']}")
    print(f"市场月薪 25/50/75 分位: "
          f"{fmt_salary(result['market']['p25'])} / {fmt_salary(result['market']['p50'])} / "
          f"{fmt_salary(result['market']['p75'])}")
    print(f"分析结果 -> {out_dir / 'analysis.json'}")


if __name__ == "__main__":
    main()
