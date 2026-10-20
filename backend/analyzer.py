
import ast
from pathlib import Path


IGNORED = {
    ".git", "venv", ".venv",
    "node_modules", "__pycache__",
    "build", "dist"
}


def analyze_repository(repo_path):
    root = Path(repo_path).resolve()
    results = []

    for file in root.rglob("*.py"):
        relative = file.relative_to(root)

        if any(part in IGNORED for part in relative.parts):
            continue

        # Don't follow symlinks outside the repository.
        if file.is_symlink():
            continue

        if file.stat().st_size > 100_000:
            continue

        if len(results) >= 30:
            break

        try:
            code = file.read_text(encoding="utf-8")
            tree = ast.parse(code)

            functions = []
            classes = []

            for node in ast.walk(tree):
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    functions.append(node.name)

                elif isinstance(node, ast.ClassDef):
                    classes.append(node.name)

            results.append({
                "file": str(relative),
                "functions": functions,
                "classes": classes
            })

        except (SyntaxError, UnicodeError, OSError):
            continue

    return results
def get_relevant_code(repo_path, issue, max_files=5):
    root = Path(repo_path).resolve()

    ignored = {
        ".git", "venv", ".venv",
        "node_modules", "__pycache__",
        "build", "dist"
    }

    keywords = {
        word.lower()
        for word in issue.split()
        if len(word) > 3
    }

    candidates = []

    for file in root.rglob("*.py"):
        relative = file.relative_to(root)

        if any(part in ignored for part in relative.parts):
            continue

        if file.is_symlink() or file.stat().st_size > 20000:
            continue

        try:
            code = file.read_text(encoding="utf-8")
        except (UnicodeError, OSError):
            continue

        # Rank files using issue keywords
        searchable = (
            str(relative).lower() + " " +
            code[:3000].lower()
        )

        score = sum(
            1 for word in keywords
            if word in searchable
        )

        candidates.append((score, str(relative), code))

    # Higher-scoring files appear first
    candidates.sort(key=lambda item: (-item[0], item[1]))

    selected = []

    for score, filename, code in candidates[:max_files]:
        selected.append({
            "file": filename,
            "code": code[:8000]
        })

    return selected
import re


def get_bug_relevant_code(repo_path, issue, max_files=5):

    root = Path(repo_path).resolve()

    ignored = {
        ".git", "venv", ".venv", "node_modules",
        "__pycache__", "build", "dist"
    }

    stop_words = {
        "this", "that", "with", "from", "when",
        "where", "what", "which", "does", "have",
        "into", "there", "error", "issue",
        "problem", "please", "fails", "working"
    }

    keywords = {
        word.lower()
        for word in re.findall(r"[A-Za-z_]+", issue)
        if len(word) > 3 and word.lower() not in stop_words
    }

    candidates = []

    for file in root.rglob("*.py"):

        relative = file.relative_to(root)

        if any(part in ignored for part in relative.parts):
            continue

        if file.is_symlink():
            continue

        try:
            if file.stat().st_size > 20000:
                continue

            code = file.read_text(encoding="utf-8")

        except (OSError, UnicodeError):
            continue

        filename = str(relative).lower()
        content = code[:5000].lower()

        score = 0

        for keyword in keywords:
            if keyword in filename:
                score += 5

            if keyword in content:
                score += 1

        # Prefer application source files over tests
        if "tests" not in relative.parts:
            score += 2

        candidates.append(
            (score, str(relative), code[:8000])
        )

    candidates.sort(
        key=lambda item: (-item[0], item[1])
    )

    return [
        {"file": filename, "code": code}
        for score, filename, code in candidates[:max_files]
    ]