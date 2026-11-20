#!/usr/bin/env python3
"""自适应 Python 运行器：找到装有分析依赖的解释器，用它执行目标脚本。

用法：
    python run.py <target_script.py> [args ...]

设计目标：**用户下载 skill 拖进 agent 即可用，不硬编码任何本机路径。**

本引导脚本只用 Python 标准库，因此可由任意 python 启动（ WorkBuddy 自带的 python、
系统 python、conda python 都行）。它负责：

1. 读取调用者传来的目标脚本与参数；
2. 解析出一个"装齐 numpy/pandas/scipy/matplotlib"的解释器：
   - 环境变量 CSV_ANALYZER_PYTHON 指定的（最高优先级，用户可强制）；
   - 上次探测并缓存的选择（skill 目录 .python_env.json）；
   - 自动扫描候选：当前 python → conda 环境 → py 启动器 → 常见路径；
   - 都没有 → 在 skill 目录建 .venv 并按 requirements.txt 安装依赖；
3. 用选中的解释器执行目标脚本，原样透传退出码与输出。

缓存失效：删除 skill 目录下的 .python_env.json 即可强制重新探测。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
CACHE = SKILL_DIR / ".python_env.json"
REQUIREMENTS = SKILL_DIR / "requirements.txt"
REQUIRED_MODULES = ("numpy", "pandas", "scipy", "matplotlib")

# 探测某个解释器能否 import 全部依赖。用 -c + importlib，避免副作用。
PROBE = "import importlib,sys; " + "; ".join(
    f"importlib.import_module('{m}')" for m in REQUIRED_MODULES
) + "; print('OK')"


def log(msg: str) -> None:
    print(f"[csv-analyzer] {msg}", file=sys.stderr)


def probe(python: str | Path, timeout: float = 25.0) -> bool:
    """返回该解释器是否装齐所有依赖。"""
    py = str(python)
    if not py:
        return False
    try:
        r = subprocess.run(
            [py, "-c", PROBE],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode == 0 and "OK" in r.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def unique(paths) -> list[Path]:
    seen, out = set(), []
    for p in paths:
        if not p:
            continue
        try:
            key = str(Path(p).resolve()).lower()
        except OSError:
            key = str(p).lower()
        if key not in seen:
            seen.add(key)
            out.append(Path(p))
    return out


def candidate_pythons() -> list[Path]:
    """按优先级收集候选解释器路径（只用标准库，跨平台）。"""
    cands: list[Path] = []

    # 0) 用户显式指定
    env_py = os.environ.get("CSV_ANALYZER_PYTHON")
    if env_py:
        cands.append(Path(env_py))

    # 1) 当前解释器（启动 run.py 的 python）
    cands.append(Path(sys.executable))

    # 2) PATH 里的 python / python3
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            cands.append(Path(found))

    # 3) conda 环境（conda env list --json）
    conda = shutil.which("conda")
    if conda:
        try:
            r = subprocess.run(
                [conda, "env", "list", "--json"],
                capture_output=True, text=True, timeout=20,
            )
            envs = json.loads(r.stdout).get("envs", [])
            for env_dir in envs:
                exe = "python.exe" if os.name == "nt" else "python"
                cands.append(Path(env_dir) / exe)
        except (OSError, ValueError, subprocess.SubprocessError):
            pass

    # 4) py 启动器枚举的 Python（Windows）
    if os.name == "nt":
        py_launcher = shutil.which("py")
        if py_launcher:
            try:
                r = subprocess.run(
                    [py_launcher, "-0p"], capture_output=True, text=True, timeout=20,
                )
                for line in r.stdout.splitlines():
                    # 形如： -V:3.13 *        C:\...\python.exe
                    if ".exe" in line or "python" in line.lower():
                        part = line.strip().split()[-1]
                        if part and (part.lower().endswith(".exe") or "python" in part.lower()):
                            cands.append(Path(part))
            except (OSError, subprocess.SubprocessError):
                pass
        # 4b) 常见 conda / python 安装位置的兜底 glob
        roots = []
        for var in ("CONDA_EXE", "CONDA_PREFIX"):
            v = os.environ.get(var)
            if v:
                roots.append(Path(v).parent.parent)
        for drive_root in (Path("C:/"), Path("D:/")):
            for sub in ("anaconda3", "Anaconda3", "miniconda3", "Miniconda3"):
                roots.append(drive_root / sub)
        for root in roots:
            try:
                cands.extend((root / "envs").glob("*/python.exe"))
                cands.append(root / "python.exe")
            except OSError:
                pass

    return unique(cands)


def from_cache() -> str | None:
    if not CACHE.exists():
        return None
    try:
        obj = json.loads(CACHE.read_text(encoding="utf-8"))
        py = obj.get("python")
        if py and Path(py).exists() and probe(py):
            return py
    except (OSError, ValueError, TypeError):
        pass
    return None


def create_venv() -> str:
    """所有候选都缺依赖时，建本地 venv 并安装 requirements.txt。"""
    venv = SKILL_DIR / ".venv"
    exe = "python.exe" if os.name == "nt" else "python"
    py = venv / ("Scripts" if os.name == "nt" else "bin") / exe
    if not py.exists():
        log("未找到含全部依赖的 Python；正在创建本地虚拟环境 .venv（仅首次，稍候）...")
        base = str(Path(sys.executable))
        r = subprocess.run([base, "-m", "venv", str(venv)], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"创建 venv 失败：{r.stderr.strip()}")
    if not probe(str(py)):
        if not REQUIREMENTS.exists():
            raise RuntimeError("缺少 requirements.txt，无法自动安装依赖")
        log("正在安装依赖 numpy/pandas/scipy/matplotlib（仅首次，稍候）...")
        r = subprocess.run(
            [str(py), "-m", "pip", "install", "-q", "-r", str(REQUIREMENTS)],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"安装依赖失败：{r.stderr.strip()[-800:]}")
    return str(py)


def resolve_python() -> str:
    # 1) 缓存命中
    cached = from_cache()
    if cached:
        return cached

    # 2) 扫描候选，选第一个装齐依赖的
    for py in candidate_pythons():
        if probe(str(py)):
            log(f"选用解释器：{py}")
            save_cache(str(py))
            return str(py)

    # 3) 兜底：建 venv 装依赖
    py = create_venv()
    save_cache(py)
    return py


def save_cache(python: str) -> None:
    try:
        CACHE.write_text(
            json.dumps({"python": python, "modules": list(REQUIRED_MODULES)},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass  # 缓存失败不影响运行


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python run.py <target_script.py> [args ...]", file=sys.stderr)
        return 2
    raw = Path(sys.argv[1])
    target = None
    if raw.is_absolute():
        target = raw
    else:
        # 依次尝试：相对当前工作目录 → 相对 skill 根目录。
        # 这样既支持在 skill 目录内写 scripts/call_tool.py，
        # 也支持从外部写 <skill>/scripts/call_tool.py。
        for base in (Path.cwd(), SKILL_DIR):
            candidate = (base / raw).resolve()
            if candidate.exists():
                target = candidate
                break
        if target is None:
            target = (SKILL_DIR / raw).resolve()
    if not target.exists():
        print(f"[csv-analyzer] 目标脚本不存在：{target}", file=sys.stderr)
        return 2

    try:
        python = resolve_python()
    except Exception as exc:
        print(f"[csv-analyzer] 无法准备 Python 环境：{exc}", file=sys.stderr)
        return 3

    # 用选中的解释器执行目标脚本，透传参数；不捕获输出，保持原样。
    try:
        completed = subprocess.run([python, str(target)] + sys.argv[2:])
        return completed.returncode
    except OSError as exc:
        print(f"[csv-analyzer] 执行失败：{exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
