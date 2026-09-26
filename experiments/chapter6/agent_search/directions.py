"""Post-search operational descriptors. Never used by the frozen S1 policy."""
from __future__ import annotations
import ast
import copy
from chapter6_demo.programs import Program,ProgramError
from chapter6_demo.v12_2.common import digest


def describe(code):
    try:
        Program(code,'tsp')
    except (ProgramError,TypeError,ValueError,RecursionError):
        return {'version':'s1-posthoc-structure-v1','direction_id':'unknown','features':[],
                'status':'outside_bounded_grammar','semantic_mode_verified':False}
    tree=ast.parse(code)
    variables={}
    class Alpha(ast.NodeTransformer):
        def visit_Name(self,node):
            if node.id in variables:
                node.id=variables[node.id]
            return node
        def visit_Assign(self,node):
            # Keep dependency order; rename local identifiers only, never features.
            self.visit(node.value)
            name=node.targets[0].id
            if name not in variables: variables[name]='v'+str(len(variables))
            node.targets[0].id=variables[name]
            return node
    alpha=Alpha().visit(copy.deepcopy(tree))
    operators=sorted({type(n.op).__name__ for n in ast.walk(tree) if isinstance(n,(ast.BinOp,ast.UnaryOp,ast.BoolOp))}
                     | {type(op).__name__ for n in ast.walk(tree) if isinstance(n,ast.Compare) for op in n.ops})
    signature={'features':sorted({n.slice.value for n in ast.walk(tree) if isinstance(n,ast.Subscript)}),
               'operators':operators,'calls':sorted({n.func.id for n in ast.walk(tree) if isinstance(n,ast.Call)}),
               'conditional_count':sum(isinstance(n,(ast.If,ast.IfExp)) for n in ast.walk(tree))}
    # Coefficients, planner tags and quality are intentionally not direction labels.
    return {'version':'s1-posthoc-structure-v1','direction_id':'structure-'+digest(signature)[:16],
            **signature,'alpha_normalized_ast_sha256':digest(ast.dump(alpha,include_attributes=False)),
            'status':'operational_signature_not_semantic_equivalence','semantic_mode_verified':False}


def lineage(nodes):
    ids={}
    for n in nodes:
        if n.get('parent_id') in ids:
            ids[n['id']]=ids[n['parent_id']]
        else:
            ids[n['id']]=n['id']
    return ids
