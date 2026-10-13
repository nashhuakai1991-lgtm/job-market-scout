"""模拟「未安装 playwright」的裸环境,验证 --check-sources 与 --help 仍可用。

背景:playwright 原本是 scrape_jobs.py 的顶层硬导入,导致未安装该库的
环境(CI 的 ubuntu runner 就是)一 import 就失败,自检步骤以退出码 1 告终。
现改为惰性导入,本脚本用于复现该场景并防止回归。

用法: python scripts/check_no_playwright.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "scrape_jobs.py"

RUNNER = '''\
import runpy, sys, importlib.abc

class _Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "playwright" or fullname.startswith("playwright."):
            raise ImportError("No module named 'playwright' (simulated)")
        return None

sys.meta_path.insert(0, _Block())
sys.argv = ["scrape_jobs.py"] + sys.argv[1:]
runpy.run_path({script!r}, run_name="__main__")
'''


def run_without_playwright(*flags: str) -> tuple[int, str]:
    """在屏蔽 playwright 的子进程中运行 scrape_jobs.py,返回 (退出码, 输出)。"""
    with tempfile.TemporaryDirectory() as td:
        runner = Path(td) / "runner.py"
        runner.write_text(RUNNER.format(script=str(SCRIPT)), encoding="utf-8")
        p = subprocess.run([sys.executable, str(runner), *flags],
                           capture_output=True, text=True, timeout=120)
        return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    ok = True

    # 1) 只读自检与帮助:无 playwright 也必须可用
    for flag in ("--check-sources", "--help"):
        code, out = run_without_playwright(flag)
        passed = code == 0
        ok &= passed
        print(f"  [{'OK' if passed else 'FAIL'}] 无 playwright 时 {flag} -> 退出码 {code}")
        if not passed:
            print("    " + out.strip()[:300])

    # 2) 真正抓取时:应给出友好提示,而不是抛 ImportError 栈
    code, out = run_without_playwright("-k", "测试关键词")
    friendly = ("playwright" in out) and ("Traceback" not in out)
    ok &= friendly
    print(f"  [{'OK' if friendly else 'FAIL'}] 缺依赖时给出友好提示(退出码 {code})")
    if not friendly:
        print("    " + out.strip()[:300])

    print("结果:" + ("全部通过" if ok else "存在失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
