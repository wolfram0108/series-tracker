"""The owner names the folders of the storage in the settings; no path of a storage is written into the code."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORAGE_PATH = re.compile(r"^/(nas|mnt|media|srv|data|tmp|home)(/|$)")
SOURCES = ("core", "modules", "scripts", "run.py")


def docstring_nodes(tree):
    return {id(node.body[0].value) for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body
            and isinstance(node.body[0], ast.Expr) and isinstance(getattr(node.body[0], "value", None), ast.Constant)}


def hardcoded(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = docstring_nodes(tree)
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip
            and STORAGE_PATH.match(node.value)]


def test_a_path_of_the_storage_is_never_written_into_the_code():
    found = [f"{path.relative_to(ROOT)}:{line}: {value}"
             for source in SOURCES for path in sorted((ROOT / source).rglob("*.py") if (ROOT / source).is_dir() else [ROOT / source])
             if "__pycache__" not in path.parts for line, value in hardcoded(path)]
    assert not found, f"folders are named in the settings, not in the code: {found}"


def test_the_guard_sees_a_path_in_code_and_not_in_a_docstring(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text('"""Lives in /nas/x."""\nROOT = "/nas/torrents"\nURL = "/api/series"\n', encoding="utf-8")
    assert hardcoded(sample) == [(2, "/nas/torrents")]
