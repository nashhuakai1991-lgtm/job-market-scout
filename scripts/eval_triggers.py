#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""触发词评测:校验 skill 的 description 能否正确区分「该触发」与「不该触发」。

用途:作为 CI 门禁。改动 SKILL.md 的 description 后若触发准确率下降,
CI 会失败,提醒同步更新 evals/trigger_cases.json。

判据:用 description 里声明的意图信号 + 排除信号做集合判定。
这是**工程近似**,不是语义理解——目的是捕捉「描述被改坏」这类回归,
而非精确评测模型调用。

用法:
    python scripts/eval_triggers.py            # 阈值 0.8,低于则 exit 1
    python scripts/eval_triggers.py --json     # 输出 JSON(CI 用)
    python scripts/eval_triggers.py --min 0.9  # 自定义阈值
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL_MD = ROOT / "SKILL.md"
CASES = ROOT / "evals" / "trigger_cases.json"

# 意图信号:出现任一即认为「像是在问岗位/薪资/匹配/职业规划」
INTENT_SEEDS = [
    "招聘", "岗位", "薪资", "薪酬", "行情", "市场调研", "求职", "投递",
    "期望", "能投", "够不够", "合理", "爬", "抓", "对比", "水平",
    "经验", "学历", "匹配", "换城市",
    "胜任", "职业规划", "有前途", "转岗", "晋升", "涨", "干这行",
    "要不要考", "可行吗", "测试工程师", "后端", "前端", "数据分析",
]
# 排除信号:出现任一即判定「不是本 skill 的目标意图」
EXCLUDE_SEEDS = [
    "写简历", "改简历", "简历里", "面试", "投递简历", "招聘启事",
    "干什么", "做什么的", "是什么", "五险一金", "工商",
    "JD 的要点", "总结一下", "项目排期", "报名条件",
]


def extract_description(text: str) -> str:
    """取出 SKILL.md frontmatter 里的 description(折叠标量 `>` 形式)。"""
    m = re.search(r"description:\s*>?\s*(.*?)(?=\n[a-zA-Z_]+:\s|\n---)", text, re.S)
    return m.group(1) if m else text


def predict(query: str, desc: str) -> tuple[bool, list[str]]:
    """返回 (是否应触发, 命中信号)。"""
    if any(e in query for e in EXCLUDE_SEEDS):
        return False, []
    hits = [s for s in INTENT_SEEDS if s in query]
    # 信号词至少要在 description 中出现过,保证判据与声明一致
    hits = [s for s in hits if s in desc]
    return (len(hits) >= 1), hits


def main() -> int:
    ap = argparse.ArgumentParser(description="触发词评测")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--min", type=float, default=0.8, help="最低可接受准确率")
    args = ap.parse_args()

    if not SKILL_MD.exists() or not CASES.exists():
        print("缺少 SKILL.md 或 evals/trigger_cases.json", file=sys.stderr)
        return 2

    desc = extract_description(SKILL_MD.read_text(encoding="utf-8"))
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    threshold = float(cases.get("recommended_threshold", args.min))

    tp = tn = fp = fn = 0
    details = []

    for group, expect in (("should_trigger", True),
                          ("should_not_trigger", False),
                          ("near_neighbor", False)):
        for q in cases.get(group, []):
            got, hits = predict(q, desc)
            ok = (got == expect)
            if expect and got:
                tp += 1
            elif expect and not got:
                fn += 1
            elif not expect and got:
                fp += 1
            else:
                tn += 1
            details.append({"group": group, "query": q, "expected": expect,
                            "got": got, "ok": ok, "signals": hits})

    total = tp + tn + fp + fn
    accuracy = (tp + tn) / total if total else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0

    result = {
        "total": total, "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        "accuracy": round(accuracy, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "threshold": threshold,
        "passed": accuracy >= threshold,
        "description_chars": len(desc),
        "failures": [d for d in details if not d["ok"]],
        "details": details,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"样本 {total} 条(正例 {tp+fn} / 负例 {tn+fp})")
        for d in details:
            mark = "PASS" if d["ok"] else "FAIL"
            print(f"  [{mark}] {d['query'][:44]}")
        print()
        print(f"准确率 {accuracy:.0%} | 精确率 {precision:.0%} | 召回率 {recall:.0%}")
        print(f"阈值   {threshold:.0%} | 结果 {'通过' if result['passed'] else '未通过'}")

    if not result["passed"]:
        print("\n未通过。若确认判据需调整,请一并更新 SKILL.md description 与 evals/trigger_cases.json。",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
