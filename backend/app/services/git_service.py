"""
git_service.py — Git интеграция через subprocess.
"""
from __future__ import annotations
import subprocess, logging
from pathlib import Path

logger = logging.getLogger(__name__)

def _run(cmd, cwd=None, timeout=15):
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        return {"ok": r.returncode == 0, "stdout": r.stdout.strip(), "stderr": r.stderr.strip()}
    except FileNotFoundError:
        return {"ok": False, "stdout": "", "stderr": "git не установлен или не найден в PATH"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "Таймаут команды git"}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e)}

def _find_repo(path=None):
    if path:
        p = Path(path).resolve()
        if (p / ".git").exists():
            return str(p)
    cwd = Path(".").resolve()
    for c in [cwd, *cwd.parents]:
        if (c / ".git").exists():
            return str(c)
    return None

def git_status(repo_path=None):
    cwd = _find_repo(repo_path)
    if not cwd:
        return {"ok": False, "error": "Git репозиторий не найден. Открой папку проекта с .git"}
    branch_r = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    branch = branch_r["stdout"] if branch_r["ok"] else "unknown"
    status_r = _run(["git", "status", "--short"], cwd=cwd)
    if not status_r["ok"]:
        return {"ok": False, "error": status_r["stderr"]}
    files = [{"status": l[:2].strip(), "file": l[3:].strip()}
             for l in status_r["stdout"].split("\n") if l.strip()]
    return {"ok": True, "branch": branch, "repo": cwd, "files": files,
            "clean": not files,
            "summary": f"Ветка: {branch} | {len(files)} изменений" if files else f"Ветка: {branch} | Чисто"}

def git_diff(repo_path=None, file_path=None):
    cwd = _find_repo(repo_path)
    if not cwd:
        return {"ok": False, "error": "Git репозиторий не найден"}
    cmd_stat = ["git", "diff", "--stat"] + ([file_path] if file_path else [])
    cmd_diff = ["git", "diff"] + ([file_path] if file_path else [])
    return {"ok": True, "stat": _run(cmd_stat, cwd=cwd)["stdout"],
            "diff": (_run(cmd_diff, cwd=cwd)["stdout"] or "Нет изменений")[:8000], "repo": cwd}

def git_log(repo_path=None, limit=10):
    cwd = _find_repo(repo_path)
    if not cwd:
        return {"ok": False, "error": "Git репозиторий не найден"}
    r = _run(["git", "log", f"--max-count={limit}", "--oneline", "--decorate"], cwd=cwd)
    if not r["ok"]:
        return {"ok": False, "error": r["stderr"]}
    commits = []
    for line in r["stdout"].split("\n"):
        if line.strip():
            parts = line.split(" ", 1)
            commits.append({"hash": parts[0], "message": parts[1] if len(parts) > 1 else ""})
    return {"ok": True, "commits": commits, "count": len(commits), "repo": cwd}

def git_commit(message, repo_path=None, add_all=True):
    cwd = _find_repo(repo_path)
    if not cwd:
        return {"ok": False, "error": "Git репозиторий не найден"}
    if not message or not message.strip():
        return {"ok": False, "error": "Сообщение коммита не может быть пустым"}
    if add_all:
        add_r = _run(["git", "add", "-A"], cwd=cwd)
        if not add_r["ok"]:
            return {"ok": False, "error": "git add failed: " + add_r["stderr"]}
    r = _run(["git", "commit", "-m", message.strip()], cwd=cwd)
    if not r["ok"]:
        return {"ok": False, "error": r["stderr"]}
    return {"ok": True, "message": message, "output": r["stdout"], "repo": cwd}

def git_branches(repo_path=None):
    cwd = _find_repo(repo_path)
    if not cwd:
        return {"ok": False, "error": "Git репозиторий не найден"}
    r = _run(["git", "branch", "-a"], cwd=cwd)
    if not r["ok"]:
        return {"ok": False, "error": r["stderr"]}
    branches = []
    current = None
    for line in r["stdout"].split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("* "):
            current = line[2:].strip()
            branches.append({"name": current, "current": True})
        else:
            branches.append({"name": line, "current": False})
    return {"ok": True, "branches": branches, "current": current, "repo": cwd}

def format_git_context(repo_path=None):
    s = git_status(repo_path)
    if not s["ok"]:
        return "Git: " + s.get("error", "недоступен")
    lines = [f"Git: {s['repo']} | Ветка: {s['branch']}"]
    if s["files"]:
        lines.append(f"Изменено {len(s['files'])} файлов:")
        for f in s["files"][:15]:
            lines.append(f"  {f['status']} {f['file']}")
    else:
        lines.append("Рабочая директория чистая")
    return "\n".join(lines)
