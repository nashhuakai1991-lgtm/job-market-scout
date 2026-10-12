#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""求职分析报告生成器 — 从 analysis.json 生成 Markdown 底稿 + HTML 报告。

用法:
    python make_report.py --analysis ./out/analysis.json --out ./out --title "北京 Java 岗位求职分析"

产出:
    out/report.md      Markdown 底稿(便于二次编辑/进 Word)
    out/report.html    单文件 HTML 报告(可直接分享/打印)

设计约束:
    - HTML 不引用任何本地绝对路径,全部内联 CSS
    - 数据缺失处显示「未标注」而不是留空或编造
    - 表格仅在需要对比时使用;长文本截断并标注
"""
from __future__ import annotations

import argparse
import html
import json
from datetime import datetime
from pathlib import Path


def fmt_salary(v) -> str:
    if not v:
        return "未标注"
    v = float(v)
    if v >= 10000:
        return f"{v/10000:.1f}万"
    return f"{int(v)}元"


def fmt_range(j: dict) -> str:
    raw = j.get("salary_raw")
    if raw:
        return raw
    if j.get("salary_min") and j.get("salary_max"):
        return f"{fmt_salary(j['salary_min'])}-{fmt_salary(j['salary_max'])}"
    return "未标注"


def esc(t) -> str:
    return html.escape(str(t)) if t is not None else "—"


# ---------------------------------------------------------------- Markdown


def build_markdown(a: dict, title: str) -> str:
    p = a["profile"]
    m = a["market"]
    adv = a["advice"]
    L: list[str] = []
    A = L.append

    A(f"# {title}")
    A("")
    A(f"生成时间:{a['generated_at']}　|　数据来源:{'、'.join(a['sample'].get('sources') or ['未知'])}")
    A("")
    A("> 本报告基于**实时抓取的真实招聘数据**生成。薪资统计仅代表本次采集样本,"
      "不等于全市场水平;所有匹配分均可追溯到具体依据。")
    A("")

    # 数据源状态(如实说明哪些源可用、哪些需登录)
    src_status = a.get("source_status") or []
    if src_status:
        A("### 数据源状态")
        A("")
        A("| 数据源 | 访问方式 | 字段完整度 | 本次结果 |")
        A("| --- | --- | --- | --- |")
        for s in src_status:
            acc = "免登录" if s.get("access") == "public" else "需登录"
            st = "完整(含 JD/技能)" if s.get("structured") else "有限(无 JD/学历经验)"
            res = f"✅ {s['jobs']} 条" if s.get("ok") else f"⚠️ 未取到 — {s.get('advice','')}"
            A(f"| {esc(s['site'])} | {acc} | {st} | {res} |")
        A("")
        # 若有需登录但未使用的源,单独提示
        login_srcs = [s for s in src_status if s.get("access") == "login" and not s.get("ok")]
        if login_srcs:
            A(f"> 提示:{'、'.join(s['site'] for s in login_srcs)} 需要登录后才能获取数据。"
              f"如需纳入分析,可先完成扫码登录再重新采集。")
            A("")

    # 一、结论摘要
    A("## 一、结论摘要")
    A("")
    d = a["distribution"]
    A(f"- 本次评估岗位 **{a['sample']['total_jobs']} 个**,薪资字段覆盖 "
      f"{a['sample']['salary_coverage']*100:.0f}%")
    A(f"- 匹配分档:强匹配 {d.get('强匹配',0)} · 较匹配 {d.get('较匹配',0)} · "
      f"一般 {d.get('一般',0)} · 弱匹配 {d.get('弱匹配',0)}")
    if m.get("p50"):
        A(f"- 本地同岗位样本月薪:25 分位 {fmt_salary(m.get('p25'))} / "
          f"中位 {fmt_salary(m.get('p50'))} / 75 分位 {fmt_salary(m.get('p75'))} / "
          f"90 分位 {fmt_salary(m.get('p90'))}")
    if p.get("expect_salary"):
        v = adv["salary"].get("verdict")
        A(f"- 你的期望月薪 **{fmt_salary(p['expect_salary'])}** → 市场判断:**{v or '样本不足'}**"
          + (f",可投岗位占比 {adv['salary'].get('reachable_ratio',0)*100:.0f}%"
             if adv["salary"].get("reachable_ratio") is not None else ""))
    A("")

    # 二、求职者画像
    A("## 二、你的求职画像")
    A("")
    A("| 项目 | 内容 |")
    A("| --- | --- |")
    A(f"| 最高学历 | {esc(p.get('degree'))} |")
    A(f"| 工作年限 | {p.get('experience_years') if p.get('experience_years') is not None else '未提供'} 年 |")
    A(f"| 目标城市 | {esc(p.get('city'))} |")
    A(f"| 期望月薪 | {fmt_salary(p.get('expect_salary'))} |")
    A(f"| 技能栈 | {'、'.join(p.get('skills') or []) or '未提供'} |")
    A("")

    # 三、薪资校准
    A("## 三、薪资期望校准")
    A("")
    if m.get("n"):
        A(f"样本量:{m['n']} 条带薪资岗位。")
        A("")
        A("| 分位 | 月薪 |")
        A("| --- | --- |")
        for label, key in (("25 分位(偏低)", "p25"), ("中位数", "p50"),
                           ("75 分位", "p75"), ("90 分位(高薪)", "p90")):
            if m.get(key):
                A(f"| {label} | {fmt_salary(m[key])} |")
        A("")
    for t in adv["salary"]["text"]:
        A(f"- {t}")
    A("")

    # 四、能力差距
    A("## 四、能力差距与补齐路径")
    A("")
    gaps = adv["gaps"]
    if gaps.get("top_missing_skills"):
        A("### 高频技能缺口")
        A("")
        A("| 技能 | 要求该技能的岗位数 |")
        A("| --- | --- |")
        for k, v in gaps["top_missing_skills"][:12]:
            A(f"| {esc(k)} | {v} |")
        A("")
    if gaps.get("top_matched_skills"):
        A("### 已命中技能(你的优势)")
        A("")
        A("| 技能 | 命中岗位数 |")
        A("| --- | --- |")
        for k, v in gaps["top_matched_skills"][:10]:
            A(f"| {esc(k)} | {v} |")
        A("")
    for t in gaps["text"]:
        A(f"- {t}")
    A("")

    # 五、投递策略
    A("## 五、投递策略")
    A("")
    for t in adv["apply"]["text"]:
        A(f"- {t}")
    A("")
    c = adv["apply"]["counts"]
    A(f"分层数量:强匹配 {c['强匹配']} · 较匹配 {c['较匹配']} · 一般 {c['一般']}")
    A("")
    if adv["apply"].get("resume_keywords"):
        A(f"**简历关键词**:{'、'.join(adv['apply']['resume_keywords'])}")
        A("")

    # 六、胜任度评估 + 职业规划(方向 3)
    cp = a.get("career") or {}
    if cp.get("competency"):
        co = cp["competency"]
        A("## 六、我能不能胜任?——胜任度评估")
        A("")
        if not co.get("available"):
            A(f"- 无法评估:{co.get('reason','样本不足')}")
        else:
            A(f"- **综合胜任度:{co['competency_score']}/100({co['band']})** — {co['verdict']}")
            A(f"- 学历门槛通过率:{co['degree_pass_rate']*100:.0f}%")
            A(f"- 经验门槛通过率:{co['experience_pass_rate']*100:.0f}%")
            if co.get("skill_coverage") is not None:
                A(f"- 技能覆盖度:{co['skill_coverage']*100:.0f}%")
            A(f"- **三项全过的岗位:{co['fully_qualified_jobs']} / {co['total_jobs']}**")
            if co.get("block_reasons"):
                rs = "、".join(f"{k} {v}个" for k, v in co["block_reasons"].items())
                A(f"- 主要卡点:{rs}")
        A("")
        A("> 胜任度 = 学历通过率 × 经验通过率 × 0.65 + 技能覆盖 × 0.35。"
          "学历与经验是「能不能投」的硬门槛,技能是加分项。")
        A("")

    plan = cp.get("career_plan") or {}
    if plan.get("level_ladder") or plan.get("text"):
        A("## 七、职业规划建议")
        A("")
        if plan.get("level_ladder"):
            A("### 职级与薪资台阶")
            A("")
            A("| 职级 | 岗位数 | 薪资中位 | 75 分位 | 样本可靠性 |")
            A("| --- | --- | --- | --- | --- |")
            for x in plan["level_ladder"]:
                rel = "充分" if x.get("reliable") else "偏少(仅供参考)"
                A(f"| {esc(x['label'])} | {x['count']} | {fmt_salary(x['p50'])} | "
                  f"{fmt_salary(x['p75'])} | {rel} |")
            A("")
        if plan.get("growth_by_experience"):
            A("### 经验段与薪资成长")
            A("")
            A("| 经验要求 | 岗位数 | 薪资中位 |")
            A("| --- | --- | --- |")
            for x in plan["growth_by_experience"]:
                A(f"| {esc(x['range'])} | {x['count']} | {fmt_salary(x['p50'])} |")
            A("")
        if plan.get("certificates"):
            A("### 样本中的资格证书要求")
            A("")
            A("| 证书 | 出现岗位数 |")
            A("| --- | --- |")
            for k, v in plan["certificates"]:
                A(f"| {esc(k)} | {v} |")
            A("")
        for t in plan.get("text") or []:
            A(f"- {t}")
        A("")

    # 城市对比
    cities = adv.get("cities") or {}
    if cities.get("rows"):
        A("## 八、城市 / 区域薪资对比")
        A("")
        A("| 城市/区域 | 岗位数 | 25 分位 | 中位 | 75 分位 |")
        A("| --- | --- | --- | --- | --- |")
        for r in cities["rows"]:
            A(f"| {esc(r['city'])} | {r['count']} | {fmt_salary(r['p25'])} | "
              f"{fmt_salary(r['p50'])} | {fmt_salary(r['p75'])} |")
        A("")
        A(f"> {cities.get('note','')}")
        A("")

    # 岗位明细
    A("## 九、岗位明细(按匹配度排序)")
    A("")
    A("| # | 匹配分 | 判断 | 岗位 | 公司 | 月薪 | 学历 | 经验 | 地点 | 来源 |")
    A("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for i, j in enumerate(a["jobs"], 1):
        title_txt = esc(j.get("title"))
        if j.get("url"):
            title_txt = f"[{title_txt}]({j['url']})"
        A(f"| {i} | {j.get('score') or '—'} | {esc(j.get('verdict'))} | {title_txt} | "
          f"{esc(j.get('company'))} | {fmt_range(j)} | {esc(j.get('education'))} | "
          f"{esc(j.get('experience'))} | {esc(j.get('location'))} | {esc(j.get('source'))} |")
    A("")

    # 数据说明
    A("## 十、数据说明与免责")
    A("")
    A("- 数据来自公开招聘页面的一次抓取,岗位可能已下架或调整,投递前请以站点实时信息为准。")
    A("- 薪资为招聘方标注区间,非最终 offer;年薪类岗位已折算为月薪;"
      "未标注薪资的岗位不计入分位统计。")
    A("- 匹配分为「薪资 30% + 学历 20% + 经验 22% + 技能 28%」的加权结果,"
      "缺失维度会自动降权,不做默认假设。")
    A("- 胜任度与职业规划基于本次样本推断,样本规模会影响结论稳定性。")
    A("- 本报告为求职辅助参考,不构成任何录用承诺。")
    A("")
    return "\n".join(L)


# ---------------------------------------------------------------- HTML

CSS = """
:root{
  --ink:#1a1d21; --ink-2:#4a5058; --ink-3:#7c848e;
  --line:#e4e7eb; --bg:#fbfbfc; --card:#ffffff;
  --accent:#c0392b; --accent-soft:#fdf0ee;
  --ok:#1e7d5a; --ok-soft:#eef7f3;
  --warn:#9a6b12; --warn-soft:#fdf6e7;
  --bad:#a8322a;
  --radius:10px;
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
       "Hiragino Sans GB","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1180px;margin:0 auto;padding:40px 24px 80px}
header{border-bottom:2px solid var(--ink);padding-bottom:20px;margin-bottom:8px}
h1{font-size:27px;margin:0 0 8px;letter-spacing:-.2px;font-weight:650}
.meta{color:var(--ink-3);font-size:13px}
.notice{
  background:var(--accent-soft);border-left:3px solid var(--accent);
  padding:12px 16px;margin:22px 0;font-size:13.5px;color:var(--ink-2);border-radius:0 var(--radius) var(--radius) 0;
}
.notice.left{border-left-color:var(--accent)}
section{margin:42px 0 0}
h2{
  font-size:18px;margin:0 0 18px;font-weight:620;
  padding-bottom:9px;border-bottom:1px solid var(--line);
}
h3{font-size:15px;margin:24px 0 10px;font-weight:620;color:var(--ink-2)}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:18px 0 6px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:15px 16px}
.kpi .k{font-size:12px;color:var(--ink-3);letter-spacing:.3px}
.kpi .v{font-size:23px;font-weight:640;margin-top:5px;letter-spacing:-.5px}
.kpi .s{font-size:12px;color:var(--ink-3);margin-top:3px}
.kpi.hl{background:var(--accent-soft);border-color:#f0d5d1}
.kpi.hl .v{color:var(--accent)}
table{width:100%;border-collapse:collapse;margin:14px 0;font-size:13.5px;background:var(--card);
      border:1px solid var(--line);border-radius:var(--radius);overflow:hidden}
th{background:#f5f6f8;text-align:left;padding:10px 12px;font-weight:620;color:var(--ink-2);
   font-size:12.5px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:10px 12px;border-bottom:1px solid #f0f1f3;vertical-align:top}
tr:last-child td{border-bottom:none}
tbody tr:hover{background:#fafbfc}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
a{color:var(--accent);text-decoration:none;border-bottom:1px solid rgba(192,57,43,.28)}
a:hover{border-bottom-color:var(--accent)}
.pill{display:inline-block;padding:2px 9px;border-radius:20px;font-size:12px;font-weight:560;
      border:1px solid transparent;white-space:nowrap}
.p-strong{background:var(--ok-soft);color:var(--ok);border-color:#cfe6db}
.p-good{background:#eef4fb;color:#25608f;border-color:#d3e2f2}
.p-mid{background:var(--warn-soft);color:var(--warn);border-color:#f0e0bd}
.p-low{background:var(--accent-soft);color:var(--bad);border-color:#f0d5d1}
.p-none{background:#f2f3f5;color:var(--ink-3);border-color:var(--line)}
.bar{height:9px;border-radius:5px;background:#eceef1;overflow:hidden;min-width:60px}
.bar>i{display:block;height:100%;background:var(--accent);border-radius:5px}
ul.adv{list-style:none;padding:0;margin:12px 0}
ul.adv li{position:relative;padding:9px 0 9px 22px;border-bottom:1px solid #f2f3f5;color:var(--ink-2)}
ul.adv li:last-child{border-bottom:none}
ul.adv li::before{content:"";position:absolute;left:6px;top:18px;width:5px;height:5px;
                  border-radius:50%;background:var(--accent)}
.gap-grid{display:grid;grid-template-columns:1fr 1fr;gap:26px}
.tag{display:inline-block;background:#f4f5f7;border:1px solid var(--line);color:var(--ink-2);
     padding:3px 10px;border-radius:6px;font-size:12.5px;margin:3px 5px 3px 0}
.tag.miss{background:var(--accent-soft);border-color:#f0d5d1;color:var(--bad)}
.tag.hit{background:var(--ok-soft);border-color:#cfe6db;color:var(--ok)}
details{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);
        padding:12px 16px;margin:10px 0}
details summary{cursor:pointer;font-weight:580;color:var(--ink-2);font-size:13.5px}
details .body{margin-top:10px;color:var(--ink-2);font-size:13px;white-space:pre-wrap;
              max-height:340px;overflow:auto;line-height:1.65}
footer{margin-top:56px;padding-top:20px;border-top:1px solid var(--line);
       color:var(--ink-3);font-size:12.5px}
@media(max-width:820px){
  .wrap{padding:24px 14px 60px}
  .gap-grid{grid-template-columns:1fr}
  h1{font-size:22px}
  table{font-size:12.5px}
  th,td{padding:8px}
  .scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
}
"""

VERDICT_CLASS = {"强匹配": "p-strong", "较匹配": "p-good", "一般": "p-mid",
                 "弱匹配": "p-low", "信息不足": "p-none"}


def build_html(a: dict, title: str) -> str:
    p, m, adv = a["profile"], a["market"], a["advice"]
    d = a["distribution"]
    H: list[str] = []
    A = H.append
    total = a["sample"]["total_jobs"]
    cov = a["sample"]["salary_coverage"] * 100

    A(f"<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">")
    A(f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">")
    A(f"<title>{esc(title)}</title><style>{CSS}</style></head><body><div class=\"wrap\">")

    A("<header>")
    A(f"<h1>{esc(title)}</h1>")
    A(f"<div class=\"meta\">生成于 {esc(a['generated_at'])}　·　"
      f"数据来源：{esc('、'.join(a['sample'].get('sources') or ['未知']))}　·　"
      f"样本 {total} 个岗位</div>")
    A("</header>")
    A("<div class=\"notice\">本报告基于实时抓取的真实招聘数据生成。薪资统计仅代表本次采集样本，"
      "不等同于全市场水平；所有匹配分均可追溯到具体依据，缺失数据不参与评分、不做假设。</div>")

    # 数据源状态
    src_status = a.get("source_status") or []
    if src_status:
        A("<section><h2>数据源状态</h2><div class=\"scroll\"><table><thead><tr>"
          "<th>数据源</th><th>访问方式</th><th>字段完整度</th><th>本次结果</th>"
          "</tr></thead><tbody>")
        for s in src_status:
            acc = ("<span class=\"pill p-strong\">免登录</span>" if s.get("access") == "public"
                   else "<span class=\"pill p-mid\">需登录</span>")
            st = "完整（含 JD/技能）" if s.get("structured") else "有限（无 JD/学历经验）"
            res = (f"<b>{s['jobs']} 条</b>" if s.get("ok")
                   else f"<span style=\"color:var(--warn)\">未取到</span>")
            A(f"<tr><td>{esc(s['site'])}</td><td>{acc}</td><td>{st}</td><td>{res}</td></tr>")
        A("</tbody></table></div>")
        login_srcs = [s for s in src_status if s.get("access") == "login" and not s.get("ok")]
        if login_srcs:
            A(f"<div class=\"meta\" style=\"margin-top:8px\">提示："
              f"{esc('、'.join(s['site'] for s in login_srcs))} 需登录后才能获取数据；"
              f"如需纳入分析，可先完成扫码登录再重新采集。</div>")
        A("</section>")

    # KPI
    A("<section><div class=\"kpis\">")
    A(f"<div class=\"kpi\"><div class=\"k\">评估岗位</div><div class=\"v\">{total}</div>"
      f"<div class=\"s\">薪资覆盖 {cov:.0f}%</div></div>")
    A(f"<div class=\"kpi\"><div class=\"k\">强匹配</div><div class=\"v\">{d.get('强匹配',0)}</div>"
      f"<div class=\"s\">建议优先投递</div></div>")
    if m.get("p50"):
        A(f"<div class=\"kpi\"><div class=\"k\">市场月薪中位</div>"
          f"<div class=\"v\">{fmt_salary(m['p50'])}</div>"
          f"<div class=\"s\">25–75 分位 {fmt_salary(m.get('p25'))}–{fmt_salary(m.get('p75'))}</div></div>")
    if p.get("expect_salary"):
        cls = " hl" if adv["salary"].get("verdict") in ("偏高", "过高", "偏低") else ""
        A(f"<div class=\"kpi{cls}\"><div class=\"k\">期望月薪</div>"
          f"<div class=\"v\">{fmt_salary(p['expect_salary'])}</div>"
          f"<div class=\"s\">市场判断：{esc(adv['salary'].get('verdict') or '样本不足')}</div></div>")
    A("</div></section>")

    # 画像
    A("<section><h2>你的求职画像</h2><div class=\"scroll\"><table><tbody>")
    rows = [("最高学历", esc(p.get("degree"))),
            ("工作年限", f"{p.get('experience_years'):g} 年" if p.get("experience_years") is not None else "未提供"),
            ("目标城市", esc(p.get("city"))),
            ("期望月薪", fmt_salary(p.get("expect_salary")))]
    for k, v in rows:
        A(f"<tr><td style=\"width:130px;color:var(--ink-3)\">{k}</td><td>{v}</td></tr>")
    sk = p.get("skills") or []
    A(f"<tr><td style=\"color:var(--ink-3)\">技能栈</td><td>"
      + ("".join(f"<span class=\"tag hit\">{esc(s)}</span>" for s in sk) if sk
         else "<span style=\"color:var(--ink-3)\">未提供</span>") + "</td></tr>")
    A("</tbody></table></div></section>")

    # 薪资校准
    A("<section><h2>薪资期望校准</h2>")
    if m.get("n"):
        A("<div class=\"scroll\"><table><thead><tr><th>分位</th><th class=\"num\">月薪</th>"
          "<th>含义</th></tr></thead><tbody>")
        for label, key, mean in (("25 分位", "p25", "低于市场多数岗位"),
                                 ("中位数", "p50", "市场主流水平"),
                                 ("75 分位", "p75", "偏上水平"),
                                 ("90 分位", "p90", "高薪岗位")):
            if m.get(key):
                A(f"<tr><td>{label}</td><td class=\"num\"><b>{fmt_salary(m[key])}</b></td>"
                  f"<td style=\"color:var(--ink-3)\">{mean}</td></tr>")
        A("</tbody></table></div>")
    A("<ul class=\"adv\">" + "".join(f"<li>{esc(t)}</li>" for t in adv["salary"]["text"]) + "</ul>")
    A("</section>")

    # 能力差距
    A("<section><h2>能力差距与补齐路径</h2>")
    gaps = adv["gaps"]
    if gaps.get("top_missing_skills") or gaps.get("top_matched_skills"):
        A("<div class=\"gap-grid\">")
        A("<div><h3>高频技能缺口</h3>")
        if gaps.get("top_missing_skills"):
            A("<div class=\"scroll\"><table><thead><tr><th>技能</th><th class=\"num\">要求岗位数</th>"
              "</tr></thead><tbody>")
            for k, v in gaps["top_missing_skills"][:10]:
                A(f"<tr><td><span class=\"tag miss\">{esc(k)}</span></td>"
                  f"<td class=\"num\">{v}</td></tr>")
            A("</tbody></table></div>")
        else:
            A("<p style=\"color:var(--ink-3)\">未发现明显缺口</p>")
        A("</div><div><h3>已命中技能（你的优势）</h3>")
        if gaps.get("top_matched_skills"):
            A("<div class=\"scroll\"><table><thead><tr><th>技能</th><th class=\"num\">命中岗位数</th>"
              "</tr></thead><tbody>")
            for k, v in gaps["top_matched_skills"][:10]:
                A(f"<tr><td><span class=\"tag hit\">{esc(k)}</span></td><td class=\"num\">{v}</td></tr>")
            A("</tbody></table></div>")
        else:
            A("<p style=\"color:var(--ink-3)\">未匹配到技能标签</p>")
        A("</div></div>")
    A("<ul class=\"adv\">" + "".join(f"<li>{esc(t)}</li>" for t in gaps["text"]) + "</ul>")
    A("</section>")

    # 投递策略
    A("<section><h2>投递策略</h2>")
    A("<ul class=\"adv\">" + "".join(f"<li>{esc(t)}</li>" for t in adv["apply"]["text"]) + "</ul>")
    c = adv["apply"]["counts"]
    A("<div class=\"kpis\">")
    for label, key in (("强匹配", "强匹配"), ("较匹配", "较匹配"), ("一般", "一般")):
        A(f"<div class=\"kpi\"><div class=\"k\">{label}</div>"
          f"<div class=\"v\">{c[key]}</div></div>")
    A("</div>")
    if adv["apply"].get("resume_keywords"):
        A("<h3>简历关键词建议</h3><div>"
          + "".join(f"<span class=\"tag\">{esc(k)}</span>" for k in adv["apply"]["resume_keywords"])
          + "</div>")
    A("</section>")

    # 胜任度评估 + 职业规划
    cp = a.get("career") or {}
    co = cp.get("competency") or {}
    if co.get("available"):
        score = co["competency_score"]
        cls = ("p-strong" if score >= 80 else "p-good" if score >= 62
               else "p-mid" if score >= 45 else "p-low")
        A("<section><h2>我能不能胜任？——胜任度评估</h2>")
        A("<div class=\"kpis\">")
        A(f"<div class=\"kpi{' hl' if score < 62 else ''}\"><div class=\"k\">综合胜任度</div>"
          f"<div class=\"v\">{score}<span style=\"font-size:14px;color:var(--ink-3)\">/100</span></div>"
          f"<div class=\"s\">{esc(co['band'])}</div></div>")
        A(f"<div class=\"kpi\"><div class=\"k\">完全达标岗位</div>"
          f"<div class=\"v\">{co['fully_qualified_jobs']}<span style=\"font-size:14px;"
          f"color:var(--ink-3)\">/{co['total_jobs']}</span></div>"
          f"<div class=\"s\">三项门槛全过</div></div>")
        A(f"<div class=\"kpi\"><div class=\"k\">学历通过率</div>"
          f"<div class=\"v\">{co['degree_pass_rate']*100:.0f}%</div></div>")
        A(f"<div class=\"kpi\"><div class=\"k\">经验通过率</div>"
          f"<div class=\"v\">{co['experience_pass_rate']*100:.0f}%</div></div>")
        A("</div>")
        A(f"<div class=\"notice\" style=\"margin-top:16px\"><b>{esc(co['verdict'])}</b>"
          + (f"　主要卡点：{esc('、'.join(f'{k} {v}个' for k, v in co['block_reasons'].items()))}"
             if co.get("block_reasons") else "")
          + "</div>")
        if co.get("skill_coverage") is not None:
            A(f"<div class=\"meta\" style=\"margin-top:8px\">技能覆盖度 "
              f"{co['skill_coverage']*100:.0f}%（技能是加分项，非否决项）</div>")
        A("</section>")

    plan = cp.get("career_plan") or {}
    if plan.get("level_ladder") or plan.get("text"):
        A("<section><h2>职业规划建议</h2>")
        if plan.get("level_ladder"):
            A("<h3>职级与薪资台阶</h3><div class=\"scroll\"><table><thead><tr>"
              "<th>职级</th><th class=\"num\">岗位数</th><th class=\"num\">薪资中位</th>"
              "<th class=\"num\">75 分位</th><th>样本可靠性</th></tr></thead><tbody>")
            for x in plan["level_ladder"]:
                rel = ("<span class=\"pill p-strong\">充分</span>" if x.get("reliable")
                       else "<span class=\"pill p-mid\">偏少</span>")
                A(f"<tr><td>{esc(x['label'])}</td><td class=\"num\">{x['count']}</td>"
                  f"<td class=\"num\"><b>{fmt_salary(x['p50'])}</b></td>"
                  f"<td class=\"num\">{fmt_salary(x['p75'])}</td><td>{rel}</td></tr>")
            A("</tbody></table></div>")
        if plan.get("growth_by_experience"):
            A("<h3>经验段与薪资成长</h3><div class=\"scroll\"><table><thead><tr>"
              "<th>经验要求</th><th class=\"num\">岗位数</th><th class=\"num\">薪资中位</th>"
              "</tr></thead><tbody>")
            for x in plan["growth_by_experience"]:
                A(f"<tr><td>{esc(x['range'])}</td><td class=\"num\">{x['count']}</td>"
                  f"<td class=\"num\"><b>{fmt_salary(x['p50'])}</b></td></tr>")
            A("</tbody></table></div>")
        if plan.get("certificates"):
            A("<h3>样本中的资格证书要求</h3><div>"
              + "".join(f"<span class=\"tag miss\">{esc(k)} ×{v}</span>"
                        for k, v in plan["certificates"])
              + "</div>")
        A("<ul class=\"adv\">"
          + "".join(f"<li>{esc(t)}</li>" for t in plan.get("text") or [])
          + "</ul>")
        A("</section>")

    # 城市对比
    cities = adv.get("cities") or {}
    if cities.get("rows"):
        A("<section><h2>城市 / 区域薪资对比</h2><div class=\"scroll\">"
          "<table><thead><tr><th>城市 / 区域</th><th class=\"num\">岗位数</th>"
          "<th class=\"num\">25 分位</th><th class=\"num\">中位</th><th class=\"num\">75 分位</th>"
          "</tr></thead><tbody>")
        for r in cities["rows"]:
            A(f"<tr><td>{esc(r['city'])}</td><td class=\"num\">{r['count']}</td>"
              f"<td class=\"num\">{fmt_salary(r['p25'])}</td>"
              f"<td class=\"num\"><b>{fmt_salary(r['p50'])}</b></td>"
              f"<td class=\"num\">{fmt_salary(r['p75'])}</td></tr>")
        A("</tbody></table></div>")
        A(f"<div class=\"meta\" style=\"margin-top:8px\">{esc(cities.get('note',''))}</div>")
        A("</section>")

    # 岗位明细
    A("<section><h2>岗位明细（按匹配度排序）</h2><div class=\"scroll\">"
      "<table><thead><tr><th class=\"num\">#</th><th class=\"num\">匹配分</th><th>判断</th>"
      "<th>岗位</th><th>公司</th><th>月薪</th><th>学历</th><th>经验</th><th>地点</th><th>来源</th>"
      "</tr></thead><tbody>")
    for i, j in enumerate(a["jobs"], 1):
        score = j.get("score")
        cls = VERDICT_CLASS.get(j.get("verdict"), "p-none")
        bar = f"<div class=\"bar\"><i style=\"width:{min(score or 0,100)}%\"></i></div>" if score else ""
        t = esc(j.get("title"))
        if j.get("url"):
            t = f"<a href=\"{esc(j['url'])}\" target=\"_blank\" rel=\"noopener\">{t}</a>"
        A(f"<tr><td class=\"num\" style=\"color:var(--ink-3)\">{i}</td>"
          f"<td class=\"num\"><b>{score if score is not None else '—'}</b>{bar}</td>"
          f"<td><span class=\"pill {cls}\">{esc(j.get('verdict'))}</span></td>"
          f"<td>{t}</td><td>{esc(j.get('company'))}</td>"
          f"<td class=\"num\">{esc(fmt_range(j))}</td>"
          f"<td>{esc(j.get('education'))}</td><td>{esc(j.get('experience'))}</td>"
          f"<td>{esc(j.get('location'))}</td>"
          f"<td style=\"color:var(--ink-3);white-space:nowrap\">{esc(j.get('source'))}</td></tr>")
    A("</tbody></table></div>")

    # JD 折叠
    with_jd = [j for j in a["jobs"] if j.get("requirements_text")]
    if with_jd:
        A("<h3>岗位要求原文</h3>")
        for j in with_jd[:25]:
            A(f"<details><summary>{esc(j.get('title'))}　·　{esc(j.get('company'))}　·　"
              f"匹配 {j.get('score')}</summary><div class=\"body\">"
              f"{esc(j.get('requirements_text'))}</div></details>")
    A("</section>")

    A("<footer>")
    A("<p><b>数据说明与免责：</b>数据来自公开招聘页面的一次抓取，岗位可能已下架或调整，"
      "投递前请以站点实时信息为准。薪资为招聘方标注区间，非最终 offer；"
      "未标注薪资的岗位不计入分位统计。</p>")
    A("<p>匹配分 = 薪资 30% + 学历 20% + 经验 22% + 技能 28% 的加权结果；"
      "某维度数据缺失时自动降权，不做默认假设。本报告仅供求职参考，不构成录用承诺。</p>")
    A("</footer></div></body></html>")
    return "\n".join(H)


def main() -> None:
    ap = argparse.ArgumentParser(description="求职分析报告生成器")
    ap.add_argument("--analysis", "-a", required=True, help="analysis.json 路径")
    ap.add_argument("--career", "-c", default=None, help="career.json 路径(可选,含胜任度与职业规划)")
    ap.add_argument("--jobs", "-j", default=None, help="jobs.json 路径(可选,用于回填数据源状态)")
    ap.add_argument("--out", "-o", default=None, help="输出目录")
    ap.add_argument("--title", default=None, help="报告标题")
    args = ap.parse_args()

    src = Path(args.analysis)
    if not src.exists():
        raise SystemExit(f"找不到 {src},请先运行 match_analyze.py")
    a = json.loads(src.read_text(encoding="utf-8"))

    # 合并胜任度与职业规划(若存在)
    career_path = Path(args.career) if args.career else src.parent / "career.json"
    if career_path.exists():
        try:
            a["career"] = json.loads(career_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass

    # source_status 存在 jobs.json 里,analysis.json 不重复存 -> 按需回填
    if not a.get("source_status"):
        jobs_path = Path(args.jobs) if args.jobs else src.parent / "jobs.json"
        if jobs_path.exists():
            try:
                jp = json.loads(jobs_path.read_text(encoding="utf-8"))
                if jp.get("source_status"):
                    a["source_status"] = jp["source_status"]
            except Exception:  # noqa: BLE001
                pass

    p = a.get("profile") or {}
    q = a.get("query") or {}
    city = p.get("city") or q.get("city") or ""
    kws = "、".join(q.get("keywords") or [])
    default_title = f"{city} {kws} 岗位市场与求职匹配分析".strip()
    title = args.title or default_title

    out_dir = Path(args.out) if args.out else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    md = build_markdown(a, title)
    (out_dir / "report.md").write_text(md, encoding="utf-8")
    html_doc = build_html(a, title)
    (out_dir / "report.html").write_text(html_doc, encoding="utf-8")

    print(f"Markdown -> {out_dir / 'report.md'}")
    print(f"HTML     -> {out_dir / 'report.html'}")
    if a.get("career"):
        print("已合并胜任度与职业规划章节")


if __name__ == "__main__":
    main()
