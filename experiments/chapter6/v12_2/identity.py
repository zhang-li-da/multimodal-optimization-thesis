"""Explicit structural identity for the existing bounded priority language."""
from __future__ import annotations

import ast
import hashlib
import platform

from chapter6_demo.programs import Program, ProgramError
from .common import digest

FORMAT = "chapter6-priority-ast-json-v1"
# Deliberate schema, not ast.dump defaults or the interpreter's _fields order.
FIELDS = {
    "Module": ("body", "type_ignores"),
    "FunctionDef": ("name", "args", "body", "decorator_list", "returns", "type_comment", "type_params"),
    "arguments": ("posonlyargs", "args", "vararg", "kwonlyargs", "kw_defaults", "kwarg", "defaults"),
    "arg": ("arg", "annotation", "type_comment"),
    "Assign": ("targets", "value", "type_comment"),
    "Name": ("id", "ctx"), "Return": ("value",),
    "If": ("test", "body", "orelse"), "Expr": ("value",),
    "Constant": ("value", "kind"), "BinOp": ("left", "op", "right"),
    "UnaryOp": ("op", "operand"), "BoolOp": ("op", "values"),
    "Compare": ("left", "ops", "comparators"), "IfExp": ("test", "body", "orelse"),
    "Call": ("func", "args", "keywords"), "Subscript": ("value", "slice", "ctx"),
    **{name: () for name in ("Load", "Store", "Add", "Sub", "Mult", "Div", "Mod",
                            "Pow", "UAdd", "USub", "Not", "And", "Or",
                            "Lt", "LtE", "Gt", "GtE", "Eq", "NotEq")},
}


def structural_tree(node):
    if isinstance(node, ast.AST):
        name = type(node).__name__
        if name not in FIELDS or set(node._fields) - set(FIELDS[name]):
            raise ValueError(f"Unregistered AST schema: {name}")
        return {"node": name, "fields": [[field, structural_tree(getattr(node, field, None))]
                                          for field in FIELDS[name]]}
    if isinstance(node, list):
        return [structural_tree(item) for item in node]
    if node is None or isinstance(node, (str, bool, int)):
        return {"type": type(node).__name__, "value": node}
    if isinstance(node, float):
        return {"type": "float", "hex": node.hex()}
    raise ValueError("Value outside the registered AST schema.")


def identity(code):
    record = {"raw_code_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
              "structural_format": FORMAT, "structural_sha256": None}
    try:
        Program(code, "tsp")  # Validate before claiming a bounded-language identity.
    except (ProgramError, ValueError, ArithmeticError):
        return record
    record["structural_sha256"] = digest({"format": FORMAT, "tree": structural_tree(ast.parse(code))})
    return record


def legacy_dump_313(node):
    """Named compatibility format for historical, validated TSP programs only."""
    if isinstance(node, ast.AST):
        fields = [f"{name}={legacy_dump_313(value)}" for name, value in ast.iter_fields(node)
                  if value is not None and value != []]
        return type(node).__name__ + "(" + ", ".join(fields) + ")"
    if isinstance(node, list):
        return "[" + ", ".join(legacy_dump_313(item) for item in node) + "]"
    return repr(node)


def legacy_hashes(code):
    tree = ast.parse(code)
    return {"python": platform.python_version(),
            "runtime_default": hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest()[:16],
            "cpython_313_show_empty_false_subset": hashlib.sha256(legacy_dump_313(tree).encode()).hexdigest()[:16]}
