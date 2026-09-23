"""A bounded Python expression subset for executable heuristic synthesis.

Programs are parsed and interpreted, never passed to Python exec/eval. The
language accepts a single priority(f) function, numeric scalar arithmetic,
local assignments, conditionals and a small fixed math-function vocabulary.
No imports, attributes, loops, files, network, reflection or generated calls.
"""
from __future__ import annotations

import ast
import hashlib
import math
import operator
from dataclasses import dataclass


class ProgramError(ValueError):
    pass


FUNCS = {"abs": abs, "min": min, "max": max, "sqrt": math.sqrt,
         "log": math.log, "log1p": math.log1p, "exp": math.exp,
         "tanh": math.tanh}
BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
       ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow}
CMP = {ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt,
       ast.GtE: operator.ge, ast.Eq: operator.eq, ast.NotEq: operator.ne}
FEATURES = {
    "binpack": ("item", "remaining", "gap", "fill", "mean_gap", "min_gap",
                "std_gap", "fraction_fitting", "position"),
    "tsp": ("distance", "return_distance", "nearest_remaining", "mean_remaining",
            "regret", "progress", "cluster_density", "spread"),
    "classification": ("centroid", "diagonal_nll", "nearest", "knn_fraction", "cosine",
                       "prior", "robust_distance", "class_spread"),
}


class Validator:
    def __init__(self, task):
        self.names = {"f"}
        self.features = set(FEATURES[task])

    def body(self, statements):
        for stmt in statements:
            if isinstance(stmt, ast.Assign):
                if len(stmt.targets) != 1 or not isinstance(stmt.targets[0], ast.Name):
                    raise ProgramError("Only scalar local assignments are allowed.")
                self.expr(stmt.value)
                name = stmt.targets[0].id
                if name == "f" or name in FUNCS or name.startswith("_"):
                    raise ProgramError("Reserved assignment name.")
                self.names.add(name)
            elif isinstance(stmt, ast.Return):
                self.expr(stmt.value)
            elif isinstance(stmt, ast.If):
                self.expr(stmt.test)
                self.body(stmt.body)
                self.body(stmt.orelse)
            elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                pass  # docstring only
            else:
                raise ProgramError(f"Statement {type(stmt).__name__} is outside the bounded language.")

    def expr(self, node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int,float,bool)):
            if not math.isfinite(float(node.value)) or abs(node.value) > 1e6:
                raise ProgramError("Numeric literal out of range.")
        elif isinstance(node, ast.Name):
            if node.id not in self.names or node.id == "f":
                raise ProgramError("Unknown scalar name.")
        elif isinstance(node, ast.Subscript):
            if not isinstance(node.value, ast.Name) or node.value.id != "f":
                raise ProgramError("Only feature subscripts f['name'] are supported.")
            if not isinstance(node.slice, ast.Constant) or node.slice.value not in self.features:
                raise ProgramError("Unknown feature key.")
        elif isinstance(node, ast.BinOp) and type(node.op) in BIN:
            self.expr(node.left)
            self.expr(node.right)
            if isinstance(node.op, ast.Pow):
                if not isinstance(node.right, ast.Constant) or not isinstance(node.right.value, (int,float)) or abs(node.right.value) > 4:
                    raise ProgramError("Exponent must be a numeric constant in [-4,4].")
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub,ast.UAdd,ast.Not)):
            self.expr(node.operand)
        elif isinstance(node, ast.Compare) and all(type(op) in CMP for op in node.ops):
            self.expr(node.left)
            for item in node.comparators:
                self.expr(item)
        elif isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And,ast.Or)):
            for item in node.values:
                self.expr(item)
        elif isinstance(node, ast.IfExp):
            for item in (node.test,node.body,node.orelse):
                self.expr(item)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCS:
            if node.keywords or not 1 <= len(node.args) <= 4:
                raise ProgramError("Math functions accept one to four positional scalars.")
            for item in node.args:
                self.expr(item)
        else:
            raise ProgramError(f"Expression {type(node).__name__} is outside the bounded language.")


@dataclass
class Program:
    code: str
    task: str

    def __post_init__(self):
        if not isinstance(self.code,str) or len(self.code) > 5000:
            raise ProgramError("Code must be a string of at most 5000 characters.")
        try:
            tree = ast.parse(self.code)
        except (SyntaxError, RecursionError) as exc:
            raise ProgramError("Python syntax error.") from exc
        if len(list(ast.walk(tree))) > 320:
            raise ProgramError("Program exceeds 320 AST nodes.")
        if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
            raise ProgramError("Provide exactly one def priority(f) function.")
        func = tree.body[0]
        if func.name != "priority" or func.decorator_list or func.returns or func.type_comment or func.type_params:
            raise ProgramError("Function must be undecorated priority(f) without annotations.")
        args = func.args
        if len(args.args) != 1 or args.args[0].arg != "f" or args.args[0].annotation or args.posonlyargs or args.kwonlyargs or args.defaults or args.kw_defaults or args.vararg or args.kwarg:
            raise ProgramError("Function must take only f.")
        if self.task not in FEATURES:
            raise ProgramError("Unknown task.")
        Validator(self.task).body(func.body)
        self.statements = func.body
        self.calls = 0
        self.ast_nodes = len(list(ast.walk(tree)))
        self.hash = hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest()[:16]

    def __call__(self, features):
        self.calls += 1
        env = {}
        count = [0]

        def expression(node):
            count[0] += 1
            if count[0] > 640:
                raise ProgramError("Interpreter operation limit.")
            if isinstance(node,ast.Constant):
                return node.value
            if isinstance(node,ast.Name):
                return env[node.id]
            if isinstance(node,ast.Subscript):
                return features[node.slice.value]
            if isinstance(node,ast.BinOp):
                value = BIN[type(node.op)](expression(node.left), expression(node.right))
                if isinstance(value, complex) or not math.isfinite(float(value)) or abs(value)>1e15:
                    raise ProgramError("Nonfinite or out-of-range intermediate arithmetic.")
                return value
            if isinstance(node,ast.UnaryOp):
                value = expression(node.operand)
                return -value if isinstance(node.op,ast.USub) else (not value if isinstance(node.op,ast.Not) else +value)
            if isinstance(node,ast.Compare):
                left = expression(node.left)
                for op,right_node in zip(node.ops,node.comparators):
                    right = expression(right_node)
                    if not CMP[type(op)](left,right):
                        return False
                    left = right
                return True
            if isinstance(node,ast.BoolOp):
                value = expression(node.values[0])
                for item in node.values[1:]:
                    if isinstance(node.op,ast.And) and not value:
                        return value
                    if isinstance(node.op,ast.Or) and value:
                        return value
                    value = expression(item)
                return value
            if isinstance(node,ast.IfExp):
                return expression(node.body if expression(node.test) else node.orelse)
            if isinstance(node,ast.Call):
                args = [expression(item) for item in node.args]
                if node.func.id == "exp" and abs(args[0]) > 50:
                    raise ProgramError("exp argument outside [-50,50].")
                value = FUNCS[node.func.id](*args)
                if not math.isfinite(float(value)) or abs(value)>1e15:
                    raise ProgramError("Invalid math result.")
                return value
            raise ProgramError("Unsupported expression.")

        def body(statements):
            for stmt in statements:
                if isinstance(stmt,ast.Assign):
                    env[stmt.targets[0].id] = expression(stmt.value)
                elif isinstance(stmt,ast.Return):
                    return True, expression(stmt.value)
                elif isinstance(stmt,ast.If):
                    returned,value = body(stmt.body if expression(stmt.test) else stmt.orelse)
                    if returned:
                        return returned,value
            return False,None

        try:
            returned,value = body(self.statements)
            if not returned or isinstance(value,complex) or not math.isfinite(float(value)):
                raise ProgramError("Function did not return a finite scalar.")
            return float(value)
        except (ArithmeticError,KeyError,TypeError,ValueError) as exc:
            if isinstance(exc,ProgramError):
                raise
            raise ProgramError(type(exc).__name__) from None


def normalized_source(code):
    """For audit display only; behavior, not syntax, defines archive niches."""
    return ast.unparse(ast.parse(code))
