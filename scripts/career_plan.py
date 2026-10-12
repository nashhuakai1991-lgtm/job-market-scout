#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职业胜任度与职业规划评估模块。

回答两个问题:
  1. 「我能不能胜任这类岗位?」—— 三维胜任度评估
  2. 「我的职业路径怎么走?」—— 纵向晋升 / 横向转岗 / 薪资成长曲线

用法(被 match_analyze.py 调用,也可单独跑):
    python career_plan.py --jobs ./out/jobs.json --analysis ./out/analysis.json --out ./out
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from match_analyze import (  # noqa: E402
    DEGREE_LABEL, fmt_salary, norm_degree, parse_exp_years, percentile,
)

# 职级关键词 -> 大致层级(越大越高),用于纵向路径推断
# 注意:匹配顺序按「层级高优先」判定,避免「储备项目经理」被「储备」拽到入门级
LEVEL_HINTS = [
    (["副总", "副总经理", "总经理", "事业部", "gm"], 7, "高管"),
    (["总监", "director", "总工", "技术总", "负责人"], 6, "总监/总工"),
    (["经理", "manager"], 5, "经理"),
    (["高级", "资深", "senior", "sr."], 4, "高级"),
    (["主管", "主办", "组长", "班长", "工程师", "技术员"], 3, "骨干/主管"),
    (["专员", "执行", "操作工", "绘图员"], 2, "专员/执行"),
    (["助理", "实习", "见习", "学徒", "管培"], 1, "入门/助理"),
]

# 管理/技术双通道判断
MGMT_HINTS = ("经理", "主管", "总监", "负责人", "总", "管理", "统筹")
TECH_HINTS = ("工程师", "技术", "设计", "绘图", "造价", "预算", "质量", "安全", "资料")


def infer_level(title: str | None) -> tuple[int, str]:
    """从岗位名推断职级。返回 (层级数字, 中文标签)。

    取**命中到的最高层级**,而不是先命中的那个 —— 「储备项目经理」同时含
    「储备」(易误判入门)与「经理」,应归为经理级。
    """
    if not title:
        return (0, "未知")
    t = title.lower()
    best = (0, "未知")
    for hints, lv, label in LEVEL_HINTS:
        for h in hints:
            if h in t:
                if lv > best[0]:
                    best = (lv, label)
                break
    return best


def infer_track(title: str | None) -> str:
    """判断偏管理还是偏技术。"""
    if not title:
        return "未知"
    t = title
    has_m = any(k in t for k in MGMT_HINTS)
    has_t = any(k in t for k in TECH_HINTS)
    if has_m and has_t:
        return "管理+技术(复合)"
    if has_m:
        return "管理线"
    if has_t:
        return "技术线"
    return "其他"


# ---------------------------------------------------------------- 胜任度


def assess_competency(profile: dict, jobs: list[dict], analysis: dict) -> dict:
    """三维胜任度:硬门槛通过率 / 竞争位次 / 能力覆盖。

    - 硬门槛(学历+经验):这是「能不能投」的否决项,权重最高
    - 竞争位次:你的条件在同组候选人里大致排什么位置
    - 能力覆盖:技能栈对岗位要求的覆盖度
    """
    total = len(jobs)
    if not total:
        return {"available": False, "reason": "无岗位样本"}

    deg_rank = profile.get("degree_rank")
    years = profile.get("experience_years")
    skills = [s.lower() for s in (profile.get("skills") or [])]

    pass_degree = pass_exp = 0
    skill_ratios: list[float] = []
    block_reasons: dict[str, int] = {}

    for j in jobs:
        # 学历门槛
        need_d = norm_degree(j.get("education"))
        d_ok = True
        if need_d is not None and deg_rank is not None:
            d_ok = deg_rank >= need_d
            if not d_ok:
                block_reasons["学历不足"] = block_reasons.get("学历不足", 0) + 1
        pass_degree += d_ok

        # 经验门槛
        pair = parse_exp_years(j.get("experience"))
        e_ok = True
        if pair and years is not None:
            lo, hi = pair
            e_ok = lo <= years <= hi
            if not e_ok:
                key = "经验不足" if years < lo else "经验高于要求"
                block_reasons[key] = block_reasons.get(key, 0) + 1
        pass_exp += e_ok

        # 技能覆盖
        tags = [t.lower() for t in (j.get("skills") or []) if t]
        if tags and skills:
            hit = sum(1 for t in tags
                      if any(t == s or (len(s) >= 2 and (s in t or t in s)) for s in skills))
            skill_ratios.append(hit / len(tags))

    deg_pass_rate = pass_degree / total
    exp_pass_rate = pass_exp / total
    skill_cov = (sum(skill_ratios) / len(skill_ratios)) if skill_ratios else None

    # 综合胜任度:学历与经验是门槛(通过率相乘),技能是加权项
    gate = deg_pass_rate * exp_pass_rate
    if skill_cov is None:
        comp = gate
    else:
        comp = gate * 0.65 + skill_cov * 0.35

    # 绝对达标岗位数(三项都过)
    fully_ok = 0
    for j in jobs:
        need_d = norm_degree(j.get("education"))
        pair = parse_exp_years(j.get("experience"))
        ok = True
        if need_d is not None and deg_rank is not None and deg_rank < need_d:
            ok = False
        if pair and years is not None and not (pair[0] <= years <= pair[1]):
            ok = False
        if ok:
            fully_ok += 1

    if comp >= 0.80:
        band, verdict = "高", "完全能胜任,可挑岗位"
    elif comp >= 0.62:
        band, verdict = "较高", "基本能胜任,个别硬门槛需规避"
    elif comp >= 0.45:
        band, verdict = "中等", "部分能胜任,需针对性补强"
    else:
        band, verdict = "偏低", "门槛差距明显,建议调整目标或先补短板"

    return {
        "available": True,
        "competency_score": round(comp * 100),
        "band": band,
        "verdict": verdict,
        "degree_pass_rate": round(deg_pass_rate, 3),
        "experience_pass_rate": round(exp_pass_rate, 3),
        "skill_coverage": round(skill_cov, 3) if skill_cov is not None else None,
        "fully_qualified_jobs": fully_ok,
        "total_jobs": total,
        "block_reasons": block_reasons,
        # 分档岗位明细(供报告展示)
        "blocked_by_degree": [
            j.get("title") for j in jobs
            if (norm_degree(j.get("education")) or 0) > (deg_rank or 0)
        ][:8],
    }


# ---------------------------------------------------------------- 职业规划


def build_career_plan(profile: dict, jobs: list[dict], analysis: dict) -> dict:
    """生成职业规划:纵向晋升路径 + 横向可转方向 + 薪资成长预期。"""
    plan: dict = {"available": True, "text": []}

    cur_years = profile.get("experience_years")
    cur_deg = profile.get("degree_rank")

    # --- 1) 纵向:按职级分层统计薪资,给出晋升台阶
    by_level: dict[int, list[float]] = {}
    level_label: dict[int, str] = {}
    for j in jobs:
        lv, lb = infer_level(j.get("title"))
        if lv == 0:
            continue
        level_label[lv] = lb
        if j.get("salary_min") and j.get("salary_max"):
            by_level.setdefault(lv, []).append((j["salary_min"] + j["salary_max"]) / 2)

    ladder = []
    for lv in sorted(by_level):
        vals = sorted(by_level[lv])
        ladder.append({
            "level": lv, "label": level_label.get(lv, f"L{lv}"),
            "count": len(vals),
            "p50": round(percentile(vals, 0.5) or 0),
            "p75": round(percentile(vals, 0.75) or 0),
            "reliable": len(vals) >= 3,   # 样本太少则不适合作结论
        })

    plan["level_ladder"] = ladder
    plan["level_ladder_thin"] = [x["label"] for x in ladder if not x["reliable"]]

    # --- 2) 纵向建议:只在样本量足够时给「晋升涨幅」结论,避免小样本误导
    # 注意:「董事长助理」这类高职级低样本岗位(单样本 3.5万)会拉偏首档,故只信 reliable 档
    solid = [x for x in ladder if x["reliable"]]
    if solid:
        plan["text"].append(
            "本次样本覆盖职级梯度:"
            + "、".join(f"{x['label']}(中位 {fmt_salary(x['p50'])}, {x['count']}个)"
                        for x in solid) + "。")
        if plan["level_ladder_thin"]:
            plan["text"].append(
                f"另有 {'、'.join(plan['level_ladder_thin'])} 档样本量不足 3 个,"
                f"已排除在梯度结论外(单样本易被个别特殊岗位拉偏)。")
        if len(solid) >= 2:
            low, high = solid[0], solid[-1]
            gap = high["p50"] - low["p50"]
            steps = high["level"] - low["level"]
            if gap > 0 and steps > 0:
                plan["text"].append(
                    f"从「{low['label']}」到「{high['label']}」,薪资中位提升约 "
                    f"{fmt_salary(gap)}(跨 {steps} 级),平均每级约 "
                    f"{fmt_salary(gap / steps)}。这是可预期的纵向空间。")
            else:
                plan["text"].append(
                    "样本内职级与薪资未呈现明显单调关系,说明该岗位的收入更多取决于"
                    "**项目规模与公司层次**而非头衔高低,谈薪时建议以项目体量作为筹码。")
    else:
        plan["text"].append(
            "各职级样本量均偏少,不足以给出可靠的晋升涨幅结论。"
            "建议扩大抓取范围(增加页数或放宽关键词)后再看职级梯度。")

    # --- 3) 横向:同薪资水平下有哪些相邻岗位方向
    mine_track = infer_track(" ".join(profile.get("skills") or []))
    tracks: dict[str, list[float]] = {}
    for j in jobs:
        tr = infer_track(j.get("title"))
        if tr in ("未知", "其他") or not j.get("salary_min"):
            continue
        tracks.setdefault(tr, []).append(j["salary_min"])
    plan["tracks"] = {
        k: {"count": len(v), "p50": round(percentile(sorted(v), 0.5) or 0)}
        for k, v in sorted(tracks.items(), key=lambda x: -len(x[1]))
    }
    if len(plan["tracks"]) >= 2:
        items = list(plan["tracks"].items())
        plan["text"].append(
            "横向看,这些相邻方向可供参考:"
            + "、".join(f"{k}({v['count']}个岗位,中位 {fmt_salary(v['p50'])})"
                        for k, v in items[:4])
            + "。转方向的成本主要在补齐该方向的核心能力,建议先以「内部转岗」或"
              "「相近项目经验」过渡,而不是裸转。")

    # --- 4) 薪资成长预期:按经验年限分段
    growth: dict[str, list[float]] = {}
    for j in jobs:
        pair = parse_exp_years(j.get("experience"))
        if not pair or not j.get("salary_min") or not j.get("salary_max"):
            continue
        lo = pair[0]
        if lo <= 1:
            key = "0-1年"
        elif lo <= 3:
            key = "1-3年"
        elif lo <= 5:
            key = "3-5年"
        elif lo <= 10:
            key = "5-10年"
        else:
            key = "10年以上"
        growth.setdefault(key, []).append((j["salary_min"] + j["salary_max"]) / 2)

    order = ["0-1年", "1-3年", "3-5年", "5-10年", "10年以上"]
    growth_rows = []
    for k in order:
        if k in growth:
            vals = sorted(growth[k])
            growth_rows.append({"range": k, "count": len(vals),
                                "p50": round(percentile(vals, 0.5) or 0)})
    plan["growth_by_experience"] = growth_rows

    # 用户所处段位与下一段位
    if cur_years is not None and growth_rows:
        cur_key = None
        for k in order:
            if k in growth:
                lo = float(re.match(r"(\d+)", k).group(1))
                if cur_years >= lo:
                    cur_key = k
        if cur_key:
            idx = [r["range"] for r in growth_rows].index(cur_key)
            cur_row = growth_rows[idx]
            plan["text"].append(
                f"你目前 {cur_years:g} 年经验,落在「{cur_key}」段,该段薪资中位 "
                f"{fmt_salary(cur_row['p50'])}。")
            if idx + 1 < len(growth_rows):
                nxt = growth_rows[idx + 1]
                delta = nxt["p50"] - cur_row["p50"]
                plan["text"].append(
                    f"进入「{nxt['range']}」段后中位可达 {fmt_salary(nxt['p50'])}"
                    + (f",还有约 {fmt_salary(delta)} 的提升空间。" if delta > 0 else "。")
                    + "该段的突破点通常不是年限本身,而是**是否能独立带项目/带团队**。")

    # --- 5) 学历/证书升级杠杆
    if cur_deg is not None and cur_deg < 3:
        blocked = sum(1 for j in jobs
                      if (norm_degree(j.get("education")) or 0) > cur_deg)
        if blocked:
            plan["text"].append(
                f"学历目前挡住 {blocked} 个岗位。在该行业,本科通常不是核心门槛,"
                f"优先考虑**职业资格证书**(如一级建造师/造价工程师)对薪资的拉动更直接。")

    # 证书类关键词检测(从业内标签里找)
    cert_counter: dict[str, int] = {}
    for j in jobs:
        for s in (j.get("skills") or []):
            if re.search(r"(建造师|造价师|注册|证书|资格|职称|安全员[A-C]?本)", str(s)):
                cert_counter[s] = cert_counter.get(s, 0) + 1
    if cert_counter:
        top_certs = sorted(cert_counter.items(), key=lambda x: -x[1])[:6]
        plan["certificates"] = top_certs
        plan["text"].append(
            "样本中出现的资格证书要求(含频次):"
            + "、".join(f"{k}({v})" for k, v in top_certs)
            + "。证书类门槛是**可主动攻克**的硬杠杆,投入产出比通常高于跳槽试错。")

    return plan


# ---------------------------------------------------------------- 入口


def load(jobs_path: str, analysis_path: str | None) -> tuple[dict, dict]:
    jobs = json.loads(Path(jobs_path).read_text(encoding="utf-8"))
    ana = {}
    if analysis_path and Path(analysis_path).exists():
        ana = json.loads(Path(analysis_path).read_text(encoding="utf-8"))
    return jobs, ana


def main() -> None:
    ap = argparse.ArgumentParser(description="职业胜任度与职业规划评估")
    ap.add_argument("--jobs", "-j", required=True)
    ap.add_argument("--analysis", "-a", default=None)
    ap.add_argument("--out", "-o", default=None)
    args = ap.parse_args()

    jobs_payload, ana = load(args.jobs, args.analysis)
    profile = ana.get("profile") or {}
    if not profile.get("degree_rank") is not None and profile.get("degree"):
        profile["degree_rank"] = norm_degree(profile["degree"])
    jobs = jobs_payload.get("jobs", [])

    comp = assess_competency(profile, jobs, ana)
    plan = build_career_plan(profile, jobs, ana)

    result = {"competency": comp, "career_plan": plan}
    out_dir = Path(args.out) if args.out else Path(args.jobs).parent
    (out_dir / "career.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"胜任度: {comp.get('competency_score')}/100 ({comp.get('band')}) "
          f"— {comp.get('verdict')}")
    print(f"学历通过率 {comp.get('degree_pass_rate')} | "
          f"经验通过率 {comp.get('experience_pass_rate')} | "
          f"技能覆盖 {comp.get('skill_coverage')}")
    print(f"完全达标岗位: {comp.get('fully_qualified_jobs')}/{comp.get('total_jobs')}")
    print(f"-> {out_dir / 'career.json'}")


if __name__ == "__main__":
    main()
