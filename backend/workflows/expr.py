"""Constrained expression + template engine. AST-whitelisted; no eval(), no imports, no dunder access."""
import ast
import operator
import re
from typing import Any

MAX_EXPR_LEN = 500
MAX_NODES = 120

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Mod: operator.mod}
_CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
        ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b, ast.Is: operator.is_, ast.IsNot: operator.is_not}
_FUNCS = {"len": len, "str": str, "int": int, "float": float, "bool": bool, "min": min, "max": max, "sum": sum, "abs": abs,
          "round": round, "sorted": sorted, "list": list}
_METHODS = {"lower", "upper", "strip", "startswith", "endswith", "split", "join", "replace", "get", "keys", "values", "items", "count", "title"}


class ExprError(ValueError):
    pass


def _check(tree: ast.AST):
    n = 0
    for node in ast.walk(tree):
        n += 1
        if n > MAX_NODES:
            raise ExprError("expression too complex")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ExprError("private attribute access is not allowed")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ExprError("name not allowed")
        if isinstance(node, (ast.Lambda, ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp, ast.Await, ast.Yield,
                             ast.NamedExpr, ast.JoinedStr, ast.Starred)):
            raise ExprError(f"{type(node).__name__} is not allowed")


def _ev(node: ast.AST, env: dict) -> Any:
    if isinstance(node, ast.Expression):
        return _ev(node.body, env)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in ("true", "True"):
            return True
        if node.id in ("false", "False"):
            return False
        if node.id in ("null", "None"):
            return None
        return env.get(node.id)
    if isinstance(node, ast.Attribute):
        base = _ev(node.value, env)
        if isinstance(base, dict):
            return base.get(node.attr)
        if base is None:
            return None
        raise ExprError(f"cannot read '.{node.attr}' on {type(base).__name__}")
    if isinstance(node, ast.Subscript):
        base = _ev(node.value, env)
        key = _ev(node.slice, env)
        try:
            return base[key] if base is not None else None
        except (KeyError, IndexError, TypeError):
            return None
    if isinstance(node, ast.BoolOp):
        vals = [_ev(v, env) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.UnaryOp):
        v = _ev(node.operand, env)
        return (not v) if isinstance(node.op, ast.Not) else (-v if isinstance(node.op, ast.USub) else +v)
    if isinstance(node, ast.BinOp):
        op = _BIN.get(type(node.op))
        if not op:
            raise ExprError("operator not allowed")
        a, b = _ev(node.left, env), _ev(node.right, env)
        if isinstance(node.op, ast.Mult) and isinstance(a, (str, list)) and isinstance(b, int) and b * len(a) > 10_000:
            raise ExprError("result too large")
        return op(a, b)
    if isinstance(node, ast.Compare):
        left = _ev(node.left, env)
        for op, comp in zip(node.ops, node.comparators):
            right = _ev(comp, env)
            try:
                ok = _CMP[type(op)](left, right)
            except TypeError:
                ok = False
            if not ok:
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return _ev(node.body, env) if _ev(node.test, env) else _ev(node.orelse, env)
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_ev(e, env) for e in node.elts]
    if isinstance(node, ast.Dict):
        return {_ev(k, env): _ev(v, env) for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.Call):
        args = [_ev(a, env) for a in node.args]
        if isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
            return _FUNCS[node.func.id](*args)
        if isinstance(node.func, ast.Attribute) and node.func.attr in _METHODS:
            obj = _ev(node.func.value, env)
            if isinstance(obj, (str, dict, list)):
                return getattr(obj, node.func.attr)(*args)
        raise ExprError("call not allowed")
    raise ExprError(f"{type(node).__name__} is not allowed")


def evaluate(expr: str, env: dict) -> Any:
    if len(expr) > MAX_EXPR_LEN:
        raise ExprError("expression too long")
    try:
        tree = ast.parse(expr.strip(), mode="eval")
    except SyntaxError as e:
        raise ExprError(f"syntax error: {e.msg}") from e
    _check(tree)
    try:
        return _ev(tree, env)
    except ExprError:
        raise
    except Exception as e:  # noqa: BLE001 — surface as a clean, typed error
        raise ExprError(f"{type(e).__name__}: {e}") from e


def validate_expr(expr: str) -> str | None:
    try:
        _check(ast.parse(expr.strip(), mode="eval"))
        return None
    except SyntaxError as e:
        return f"syntax error: {e.msg}"
    except ExprError as e:
        return str(e)


_TPL = re.compile(r"\{\{(.+?)\}\}", re.S)


def render(value: Any, env: dict) -> Any:
    """Recursively render {{ expr }} templates. A string that is exactly one template keeps its native type."""
    if isinstance(value, str):
        m = _TPL.fullmatch(value.strip())
        if m:
            return evaluate(m.group(1), env)

        def sub(mm):
            v = evaluate(mm.group(1), env)
            if isinstance(v, (dict, list)):
                import json
                return json.dumps(v, default=str)
            return "" if v is None else str(v)
        return _TPL.sub(sub, value)
    if isinstance(value, dict):
        return {k: render(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, env) for v in value]
    return value


def template_errors(value: Any) -> list[str]:
    errs: list[str] = []
    if isinstance(value, str):
        for m in _TPL.finditer(value):
            e = validate_expr(m.group(1))
            if e:
                errs.append(f"{{{{{m.group(1).strip()}}}}}: {e}")
    elif isinstance(value, dict):
        for v in value.values():
            errs += template_errors(v)
    elif isinstance(value, list):
        for v in value:
            errs += template_errors(v)
    return errs
