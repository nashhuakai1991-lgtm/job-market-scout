#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""安全守卫:确认仓库里没有登录态、抓取产物或疑似令牌。

为什么需要:采集脚本会用到 Playwright 的持久化 profile,里面存着
招聘站点的 Cookie 与令牌;`out/`、`raw/` 里是每次运行产生的中间数据。
这些一旦被提交就是实打实的凭据泄露。

检查项:
  1. 不存在 profile/ out/ raw/ 等目录
  2. 文件内容里没有疑似令牌字段(accessToken / refreshToken / clientToken 等)

用法: python scripts/check_safety.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()

FORBIDDEN_DIRS = {"profile", "out", "raw", ".playwright", "chromium-profile"}
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache"}

# 用拼接写法,避免本文件自身被扫描命中
TOKEN_PATTERNS = [
    re.compile(r"access" + r"[Tt]oken"),
    re.compile(r"refresh" + r"[Tt]oken"),
    re.compile(r"client" + r"[Tt]oken"),
    re.compile(r"secret" + r"[Kk]ey"),
]
TEXT_SUFFIX = {".py", ".md", ".json", ".yaml", ".yml", ".txt", ".cfg", ".ini", ".toml", ".env"}


def iter_files():
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        parts = set(p.parts)
        if parts & SKIP_DIRS:
            continue
        yield p


def main() -> int:
    problems: list[str] = []

    # 1) 禁止出现的目录
    for name in sorted(FORBIDDEN_DIRS):
        for hit in ROOT.rglob(name):
            if hit.is_dir() and not (set(hit.parts) & SKIP_DIRS):
                problems.append(f"存在不应入库的目录: {hit.relative_to(ROOT).as_posix()}")

    # 2) 疑似令牌
    scanned = 0
    for f in iter_files():
        if f.resolve() == SELF:
            continue
        if f.suffix.lower() not in TEXT_SUFFIX:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        scanned += 1
        for pat in TOKEN_PATTERNS:
            if pat.search(text):
                problems.append(
                    f"疑似令牌内容: {f.relative_to(ROOT).as_posix()} 匹配 /{pat.pattern}/")

    print(f"扫描 {scanned} 个文本文件")
    if not problems:
        print("  ✅ 未发现登录态、产物目录或疑似令牌")
        return 0

    print("  ❌ 发现问题:")
    for p in problems:
        print(f"     - {p}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
