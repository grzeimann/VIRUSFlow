from __future__ import annotations

"""
YAML-driven QA engine.

Responsibilities:
- Load declarative QA config (YAML)
- Extract metrics from inputs (v1: algorithm meta only)
- Evaluate boolean checks with a safe expression evaluator
- Determine per-kind status with policy (hard|soft|off)
- Return decision and allow persistence via DiagnosticsFacade
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import math
import operator

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore


@dataclass(frozen=True)
class Decision:
    kind: str
    policy: str  # hard | soft | off
    status: str  # pass | warn | fail | error
    metrics: Dict[str, Any]
    messages: List[str]

    @property
    def should_block(self) -> bool:
        return (self.policy == "hard") and (self.status == "fail")


class QAEngine:
    def __init__(self, yaml_path: Optional[str] = None) -> None:
        self.yaml_path = yaml_path or _discover_default_yaml()
        self._cfg = _load_yaml(self.yaml_path)

    def policy_for(self, kind: str) -> str:
        k = (kind or "").strip().lower()
        dfl_raw = (self._cfg.get("defaults") or {}).get("policy")
        # Normalize default policy (handle YAML 1.1 bools like 'off' -> False)
        if isinstance(dfl_raw, bool):
            dfl = "off" if dfl_raw is False else "soft"
        else:
            dfl = str(dfl_raw or "soft").lower()
        sec = (self._cfg.get("kinds") or {}).get(k) or {}
        pol_raw = sec.get("policy")
        if isinstance(pol_raw, bool):
            pol = "off" if pol_raw is False else dfl
        elif isinstance(pol_raw, str):
            pol = pol_raw.lower()
        else:
            pol = dfl
        return pol

    def evaluate(self, *, kind: str, meta: Optional[Dict[str, Any]] = None) -> Decision:
        k = (kind or "").strip().lower()
        sec = (self._cfg.get("kinds") or {}).get(k) or {}
        policy = self.policy_for(k)
        if policy == "off":
            return Decision(kind=k, policy=policy, status="pass", metrics=dict(meta or {}), messages=["qa off"])

        # Build metric namespace
        metrics = _extract_metrics(sec.get("metrics"), meta or {})
        # Evaluate checks
        checks: List[Tuple[str, str, str]] = []  # (id, expr, severity)
        for item in (sec.get("checks") or []):
            try:
                cid = str(item.get("id") or "check").strip()
                expr = str(item.get("where") or "True").strip()
                sev = str(item.get("severity") or "fail_if_false").strip().lower()
                checks.append((cid, expr, sev))
            except Exception:
                continue
        fired_warn = False
        fired_fail = False
        messages: List[str] = []
        for cid, expr, sev in checks:
            ok, detail = _safe_eval(expr, metrics)
            if sev == "warn_if_false":
                if not ok:
                    fired_warn = True
                    messages.append(detail or f"warn: {cid}")
            else:  # default fail_if_false
                if not ok:
                    fired_fail = True
                    messages.append(detail or f"fail: {cid}")
        status = "pass"
        if fired_fail:
            status = "fail"
        elif fired_warn:
            status = "warn"
        return Decision(kind=k, policy=policy, status=status, metrics=metrics, messages=messages)


# ---------------- config helpers ----------------

def _discover_default_yaml() -> str:
    # Env var wins
    import os
    p = os.environ.get("VF_QA_YAML")
    if p:
        return p
    # Fallback to repo docs/qa_default.yml (relative to this file: ../../docs/qa_default.yml)
    here = Path(__file__).resolve()
    candidate = here.parents[2] / "docs" / "qa_default.yml"
    return str(candidate)


@lru_cache(maxsize=4)
def _load_yaml(path: str) -> Dict[str, Any]:
    if yaml is None:
        return {"version": 1, "kinds": {}}
    try:
        text = Path(path).read_text()
        cfg = yaml.safe_load(text) or {}
        if not isinstance(cfg, dict):
            return {"version": 1, "kinds": {}}
        return cfg
    except Exception:
        return {"version": 1, "kinds": {}}


# ---------------- metric extraction ----------------

def _get_in(d: Dict[str, Any], dotted: str) -> Any:
    parts = [p for p in str(dotted).split(".") if p]
    cur: Any = d
    for p in parts:
        if isinstance(cur, dict) and (p in cur):
            cur = cur[p]
        else:
            return None
    return cur


def _reduce(name: str, val: Any, *args: Any) -> Any:
    import numpy as np
    a = np.asarray(val)
    if name == "median":
        with np.errstate(all="ignore"):
            return float(np.nanmedian(a)) if a.size else None
    if name == "percentile":
        q = float(args[0]) if args else 50.0
        with np.errstate(all="ignore"):
            return float(np.nanpercentile(a, q)) if a.size else None
    return None


def _extract_metrics(spec: Optional[Dict[str, Any]], meta: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    m = meta or {}
    for name, desc in (spec or {}).items():
        try:
            src = desc.get("from") if isinstance(desc, dict) else None
            default = desc.get("default") if isinstance(desc, dict) else None
            if not src:
                continue
            s = str(src)
            if s.startswith("meta."):
                key = s[len("meta.") :]
                out[name] = _get_in(m, key)
            elif s.startswith("reduce."):
                # format: reduce.func(meta.key)[,extra]
                call = s[len("reduce.") :]
                if call.startswith("median("):
                    inside = call[len("median(") : -1]
                    val = _get_in(m, inside.replace("meta.", "", 1))
                    out[name] = _reduce("median", val)
                elif call.startswith("percentile("):
                    inside = call[len("percentile(") : -1]
                    if "," in inside:
                        left, right = inside.split(",", 1)
                        val = _get_in(m, left.strip().replace("meta.", "", 1))
                        q = float(right)
                        out[name] = _reduce("percentile", val, q)
                    else:
                        val = _get_in(m, inside.strip().replace("meta.", "", 1))
                        out[name] = _reduce("percentile", val, 50.0)
                else:
                    out[name] = None
            else:
                out[name] = None
            if out[name] is None and default is not None:
                out[name] = default
        except Exception:
            out[name] = desc.get("default") if isinstance(desc, dict) else None
    return out


# ---------------- safe evaluator ----------------

_ALLOWED_NAMES = {
    "True": True,
    "False": False,
    "None": None,
    "nan": float("nan"),
}



def _safe_eval(expr: str, ns: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    import ast

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.ok = True

        def generic_visit(self, node):
            # Allow top-level Expression produced by ast.parse(..., mode="eval") and simple expr/load nodes
            if isinstance(node, (ast.Module, ast.Expression, ast.Expr, ast.Load)):
                super().generic_visit(node)
                return
            if isinstance(node, (ast.BoolOp, ast.UnaryOp, ast.BinOp, ast.Compare, ast.Name, ast.Constant, ast.And, ast.Or, ast.Not, ast.Eq, ast.NotEq, ast.Is, ast.IsNot, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow, ast.USub, ast.UAdd)):
                super().generic_visit(node)
                return
            # Everything else is disallowed
            self.ok = False

    try:
        tree = ast.parse(expr, mode="eval")
    except Exception:
        return False, f"invalid expression: {expr}"

    v = _Visitor()
    v.visit(tree)
    if not v.ok:
        return False, f"disallowed expression: {expr}"

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return ns.get(node.id, _ALLOWED_NAMES.get(node.id))
        if isinstance(node, ast.UnaryOp):
            val = _eval(node.operand)
            if isinstance(node.op, ast.Not):
                return not bool(val)
            if isinstance(node.op, ast.USub):
                return -val
            if isinstance(node.op, ast.UAdd):
                return +val
            raise ValueError("bad unary op")
        if isinstance(node, ast.BinOp):
            left = _eval(node.left)
            right = _eval(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.Mod):
                return left % right
            if isinstance(node.op, ast.Pow):
                return left ** right
            raise ValueError("bad bin op")
        if isinstance(node, ast.BoolOp):
            vals = [_eval(v) for v in node.values]
            if isinstance(node.op, ast.And):
                return all(bool(x) for x in vals)
            if isinstance(node.op, ast.Or):
                return any(bool(x) for x in vals)
            raise ValueError("bad bool op")
        if isinstance(node, ast.Compare):
            left = _eval(node.left)
            ok = True
            cur = left
            for op, comp in zip(node.ops, node.comparators):
                right = _eval(comp)
                if isinstance(op, ast.Eq):
                    ok = ok and (cur == right)
                elif isinstance(op, ast.NotEq):
                    ok = ok and (cur != right)
                elif isinstance(op, ast.Is):
                    ok = ok and (cur is right)
                elif isinstance(op, ast.IsNot):
                    ok = ok and (cur is not right)
                elif isinstance(op, ast.Lt):
                    ok = ok and (cur < right)
                elif isinstance(op, ast.LtE):
                    ok = ok and (cur <= right)
                elif isinstance(op, ast.Gt):
                    ok = ok and (cur > right)
                elif isinstance(op, ast.GtE):
                    ok = ok and (cur >= right)
                else:
                    raise ValueError("bad compare op")
                cur = right
            return ok
        raise ValueError("unsupported expression")

    try:
        ok = bool(_eval(tree))
        return ok, None
    except Exception as e:
        return False, f"eval error: {e}"
