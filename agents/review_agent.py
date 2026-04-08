"""ReviewAgent — heuristic static checks against REVIEW_RULES.md.

Scans the production source dirs and emits a list of `Finding` records. Each
finding has a stable hash so the approval queue can persist accept/reject
decisions across runs.

The checks here are intentionally conservative — they aim for *low false
positive rate*, not exhaustive coverage. Rules that need semantic understanding
(CR-001 hardcoded magic numbers, CR-004 lookahead bias, CR-005 sizing errors,
CR-006 None propagation, WR-003 metrics denominators, WR-005 placeholder code,
WR-006 duplicate logic) are documented in REVIEW_RULES.md as the spec, but not
yet automated — they remain in the doc as a target for future iterations.

Currently automated:
  WR-001  N+1 DB query patterns
  WR-002  Hardcoded timeframe strings outside config/
  WR-004  Bare except / `except Exception: pass`
  SG-001  Missing type hints on public function signatures
  SG-002  Functions longer than 60 lines
  SG-003  Missing docstrings on public functions
"""
from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SCAN_DIRS = ["agents", "analysis", "backtest", "engine", "data"]

# WR-002: timeframe literals that should come from config
TF_PATTERN = re.compile(r"""['"](?:15m|1h|4h|1d|1w|15M|1H|4H|1D|1W)['"]""")

# DB session method names that, if called inside a Python `for` loop body,
# strongly suggest an N+1 pattern (WR-001).
N_PLUS_ONE_METHODS = {"add", "merge", "commit", "flush", "execute"}


@dataclass
class Finding:
    rule_id: str
    severity: str       # CRITICAL | WARNING | SUGGESTION
    file: str           # repo-relative POSIX path
    line: int
    symbol: str         # function/class name when known, else ""
    message: str
    snippet: str = ""

    @property
    def hash(self) -> str:
        """Stable hash — survives line-number drift as long as the symbol
        and rule are unchanged. Falls back to line number if no symbol."""
        key = f"{self.rule_id}|{self.file}|{self.symbol or self.line}"
        return hashlib.sha1(key.encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["hash"] = self.hash
        return d


SEVERITY_OF = {
    "CR": "CRITICAL",
    "WR": "WARNING",
    "SG": "SUGGESTION",
}


def _severity(rule_id: str) -> str:
    return SEVERITY_OF.get(rule_id.split("-")[0], "SUGGESTION")


# ---------- AST checks ----------------------------------------------------

class _FunctionVisitor(ast.NodeVisitor):
    """Walks a module collecting function-level findings."""

    def __init__(self, rel_path: str, source_lines: list[str]):
        self.rel_path = rel_path
        self.lines = source_lines
        self.findings: list[Finding] = []
        self._stack: list[str] = []

    # qualified name helper -------------------------------------------------
    def _qual(self, name: str) -> str:
        return ".".join(self._stack + [name]) if self._stack else name

    def visit_ClassDef(self, node: ast.ClassDef):
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._check_function(node)
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._check_function(node)
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_ExceptHandler(self, node: ast.ExceptHandler):
        # WR-004: bare except, or `except Exception: pass`
        is_bare = node.type is None
        is_swallowed = (
            len(node.body) == 1
            and isinstance(node.body[0], ast.Pass)
        )
        if is_bare or is_swallowed:
            kind = "bare except" if is_bare else "exception swallowed with pass"
            self.findings.append(Finding(
                rule_id="WR-004",
                severity="WARNING",
                file=self.rel_path,
                line=node.lineno,
                symbol=self._qual(""),
                message=f"Silent failure: {kind}.",
                snippet=self.lines[node.lineno - 1].strip()
                    if node.lineno - 1 < len(self.lines) else "",
            ))
        self.generic_visit(node)

    def visit_For(self, node: ast.For):
        # WR-001: session.* call inside a for-loop body
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                attr = sub.func.attr
                value = sub.func.value
                if attr in N_PLUS_ONE_METHODS and isinstance(value, ast.Name):
                    if "session" in value.id.lower():
                        self.findings.append(Finding(
                            rule_id="WR-001",
                            severity="WARNING",
                            file=self.rel_path,
                            line=sub.lineno,
                            symbol=self._qual(""),
                            message=(
                                f"Possible N+1: `{value.id}.{attr}(...)` called "
                                f"inside a for-loop. Consider bulk insert/upsert."
                            ),
                            snippet=self.lines[sub.lineno - 1].strip()
                                if sub.lineno - 1 < len(self.lines) else "",
                        ))
                        break  # one finding per loop is enough
        self.generic_visit(node)

    # function-level checks -------------------------------------------------
    def _check_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef):
        is_public = not node.name.startswith("_")
        qual = self._qual(node.name)

        # SG-002 length
        end = getattr(node, "end_lineno", node.lineno)
        length = end - node.lineno + 1
        if length > 60:
            self.findings.append(Finding(
                rule_id="SG-002",
                severity="SUGGESTION",
                file=self.rel_path,
                line=node.lineno,
                symbol=qual,
                message=f"Function `{node.name}` is {length} lines (>60). Consider splitting.",
            ))

        if is_public:
            # SG-003 docstring
            if not (node.body and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)):
                self.findings.append(Finding(
                    rule_id="SG-003",
                    severity="SUGGESTION",
                    file=self.rel_path,
                    line=node.lineno,
                    symbol=qual,
                    message=f"Public function `{node.name}` has no docstring.",
                ))

            # SG-001 type hints — at least one arg unannotated, or no return ann
            args = [a for a in node.args.args if a.arg not in ("self", "cls")]
            missing_arg = [a.arg for a in args if a.annotation is None]
            missing_return = node.returns is None
            if missing_arg or missing_return:
                bits = []
                if missing_arg:
                    bits.append(f"args missing hints: {', '.join(missing_arg)}")
                if missing_return:
                    bits.append("no return annotation")
                self.findings.append(Finding(
                    rule_id="SG-001",
                    severity="SUGGESTION",
                    file=self.rel_path,
                    line=node.lineno,
                    symbol=qual,
                    message=f"`{node.name}`: " + "; ".join(bits),
                ))


# ---------- file-level regex checks ---------------------------------------

def _check_timeframe_literals(rel_path: str, source: str) -> list[Finding]:
    """WR-002: timeframe strings hardcoded outside config/."""
    out: list[Finding] = []
    if rel_path.startswith("config/"):
        return out
    for i, line in enumerate(source.splitlines(), start=1):
        # ignore comments
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        m = TF_PATTERN.search(line)
        if m:
            out.append(Finding(
                rule_id="WR-002",
                severity="WARNING",
                file=rel_path,
                line=i,
                symbol="",
                message=f"Hardcoded timeframe literal {m.group(0)} — should come from config.",
                snippet=stripped[:120],
            ))
    return out


# ---------- top-level scanner ---------------------------------------------

def scan(repo_root: Path | None = None) -> list[Finding]:
    """Walk SCAN_DIRS and return all findings, deduped by hash."""
    root = repo_root or REPO_ROOT
    findings: list[Finding] = []
    for sub in SCAN_DIRS:
        d = root / sub
        if not d.is_dir():
            continue
        for path in sorted(d.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(root).as_posix()
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError as exc:
                findings.append(Finding(
                    rule_id="WR-004",
                    severity="WARNING",
                    file=rel,
                    line=exc.lineno or 1,
                    symbol="",
                    message=f"SyntaxError while parsing: {exc.msg}",
                ))
                continue
            visitor = _FunctionVisitor(rel, source.splitlines())
            visitor.visit(tree)
            findings.extend(visitor.findings)
            findings.extend(_check_timeframe_literals(rel, source))

    # dedupe by hash (stable order)
    seen: set[str] = set()
    unique: list[Finding] = []
    for f in findings:
        if f.hash in seen:
            continue
        seen.add(f.hash)
        unique.append(f)
    return unique


def summarise(findings: list[Finding]) -> dict:
    """Group findings by severity for reporting."""
    out = {"CRITICAL": [], "WARNING": [], "SUGGESTION": []}
    for f in findings:
        out.setdefault(f.severity, []).append(f)
    return out
