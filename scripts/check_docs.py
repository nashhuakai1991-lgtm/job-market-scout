#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文档自检:校验 README 内部链接指向的文件确实存在于仓库中。

为什么需要:曾出现过「README 里列了某个示例文件,但它被 .gitignore 误伤、
实际并未入库」的情况(见 6519449 提交)。纯文档承诺与实际内容不一致很难靠肉眼发现,
这类脚本就是用来兜底的。

判定以 git 索引(git ls-files)为准,而不是文件系统:
索引是「这个文件真的在仓库里吗」的权威答案,且能避开文件系统大小写、
Unicode 规范化(NFC/NFD)带来的跨平台差异。

用法:
    python scripts/check_docs.py           # 校验 README.md
    python scripts/check_docs.py file.md   # 校验指定文档
"""
from __future__ import annotations

import re
import subprocess
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINK_RE = re.compile(r"\]\(([^)#]+)\)")
SKIP_PREFIX = ("http://", "https://", "mailto:")


def tracked_files() -> set[str]:
    """从 git 索引取出全部受版本控制的文件路径(NFC 归一)。

    注意两个 CI 上的坑:
      1. `safe.directory` —— runner 的 checkout 目录属主与执行用户可能不一致,
         git 会以「detected dubious ownership」拒绝操作,stdout 为空。
         这里显式放开,并在结果为空时给出可读诊断。
      2. 拿到空集合时不能直接判定「全部链接失效」——那会让本检查变成噪声源,
         必须与文件系统互相印证。
    """
    proc = subprocess.run(
        ["git", "-c", "safe.directory=*", "ls-files", "-z"],
        capture_output=True, cwd=ROOT,
    )
    paths: set[str] = set()
    for raw in proc.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            paths.add(unicodedata.normalize("NFC", raw.decode("utf-8")))
        except UnicodeDecodeError:
            paths.add(raw.decode("utf-8", "surrogateescape"))

    if not paths:
        err = proc.stderr.decode("utf-8", "replace").strip()
        print(f"  ⚠️  git ls-files 未返回任何结果(rc={proc.returncode})")
        if err:
            print(f"     git stderr: {err.splitlines()[0]}")
        print("     回退到文件系统判定")
        return fs_files()
    return paths


def fs_files() -> set[str]:
    """文件系统兜底:遍历仓库内所有文件(跳过 .git 与 __pycache__)。"""
    out: set[str] = set()
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        if ".git" in p.parts or "__pycache__" in p.parts:
            continue
        out.add(unicodedata.normalize("NFC", p.relative_to(ROOT).as_posix()))
    return out



def check(doc: Path, tracked: set[str]) -> int:
    md = doc.read_text(encoding="utf-8")
    checked, bad = 0, []
    for link in LINK_RE.findall(md):
        if link.startswith(SKIP_PREFIX):
            continue
        target = unicodedata.normalize("NFC", link.replace("%20", " "))
        checked += 1
        if target not in tracked:
            bad.append(link)

    print(f"{doc.name}: 检查 {checked} 条本地链接,索引内文件 {len(tracked)} 个")
    if not bad:
        print("  ✅ 全部有效")
        return 0

    print("  ❌ 以下链接在仓库中不存在:")
    for b in bad:
        print(f"     - {b}")
    print("  仓库中实际存在的示例文件:")
    for n in sorted(x for x in tracked if x.startswith("examples/")):
        print(f"     * {n}")
    print("  提示:若该文件本应入库,检查是否被 .gitignore 误伤"
          "（用 git check-ignore -v <路径> 查命中规则）")
    return 1


def main() -> int:
    docs = [ROOT / a for a in sys.argv[1:]] or [ROOT / "README.md"]
    tracked = tracked_files()
    rc = 0
    for d in docs:
        if not d.exists():
            print(f"找不到文档: {d}", file=sys.stderr)
            rc = 1
            continue
        rc |= check(d, tracked)
    return rc


if __name__ == "__main__":
    sys.exit(main())
