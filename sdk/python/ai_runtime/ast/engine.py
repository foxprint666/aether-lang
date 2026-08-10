"""
ai_runtime.ast.engine
~~~~~~~~~~~~~~~~~~~~~
AST-based application engine using libcst to preserve formatting and comments.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
import libcst as cst

def apply_patch(patch: dict, project_root: str) -> None:
    """
    Applies the patch to the target file.
    """
    target = patch.get("target", {})
    action = patch.get("action", "")
    file_path = Path(project_root) / target["file"]
    
    if not file_path.exists():
        if action in ("add_function", "modify_class") and not file_path.parent.exists():
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.touch()
        elif action != "add_function":
            raise FileNotFoundError(f"Target file not found: {file_path}")
            
    # Read original source
    source = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
    if not source.strip():
        # Handle empty files by providing a base structure
        source = ""

    # Parse with libcst
    module = cst.parse_module(source)
    
    # Dispatch
    if action == "modify_function":
        module = _apply_modify_function(module, target, patch.get("changes", {}))
    elif action == "add_function":
        module = _apply_add_function(module, target, patch.get("changes", {}))
    elif action == "remove_function":
        module = _apply_remove_function(module, target)
    elif action == "modify_class":
        module = _apply_modify_class(module, target, patch.get("changes", {}))
    elif action == "update_import":
        module = _apply_update_import(module, patch.get("changes", {}))
    elif action == "replace_block":
        module = _apply_replace_block(module, patch.get("changes", {}))
    else:
        raise ValueError(f"Unsupported action: {action}")
        
    # Write back
    file_path.write_text(module.code, encoding="utf-8")


class FunctionBodyReplacer(cst.CSTTransformer):
    def __init__(self, target_name: str, new_body_cst: cst.BaseSuite):
        self.target_name = target_name
        self.new_body_cst = new_body_cst
        self.found = False

    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef:
        if original_node.name.value == self.target_name:
            self.found = True
            return updated_node.with_changes(body=self.new_body_cst)
        return updated_node

class ClassBodyReplacer(cst.CSTTransformer):
    def __init__(self, target_name: str, new_body_cst: cst.BaseSuite):
        self.target_name = target_name
        self.new_body_cst = new_body_cst
        self.found = False

    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        if original_node.name.value == self.target_name:
            self.found = True
            return updated_node.with_changes(body=self.new_body_cst)
        return updated_node

class FunctionRemover(cst.CSTTransformer):
    def __init__(self, target_name: str):
        self.target_name = target_name
        self.removed = False

    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef) -> cst.FunctionDef | cst.RemovalSentinel:
        if original_node.name.value == self.target_name:
            self.removed = True
            return cst.RemoveFromParent()
        return updated_node

def _apply_modify_function(module: cst.Module, target: dict, changes: dict) -> cst.Module:
    operation = changes.get("operation")
    payload = changes.get("payload", "")
    symbol = target.get("symbol")
    
    if not symbol:
        raise ValueError("modify_function requires target.symbol")
        
    if operation == "replace_body":
        # We wrap payload in a dummy function to parse its body
        indented_payload = textwrap.indent(payload.strip(), "    ")
        dummy_code = f"def dummy():\n{indented_payload}"
        try:
            dummy_module = cst.parse_module(dummy_code)
            new_body = dummy_module.body[0].body # type: ignore
        except Exception as e:
            raise ValueError(f"Failed to parse new function body: {e}")
            
        transformer = FunctionBodyReplacer(symbol, new_body)
        new_module = module.visit(transformer)
        if not transformer.found:
            raise ValueError(f"Function {symbol} not found in target file")
        return new_module
    else:
        raise NotImplementedError(f"Operation {operation} not fully implemented for modify_function")

def _apply_add_function(module: cst.Module, target: dict, changes: dict) -> cst.Module:
    payload = changes.get("payload", "").strip()
    try:
        new_func_module = cst.parse_module(payload)
        new_func = new_func_module.body[0]
    except Exception as e:
        raise ValueError(f"Failed to parse new function: {e}")
        
    new_body = list(module.body)
    if new_body:
        new_func = new_func.with_changes(leading_lines=[cst.EmptyLine()])
        
    new_body.append(new_func)
    return module.with_changes(body=new_body)

def _apply_remove_function(module: cst.Module, target: dict) -> cst.Module:
    symbol = target.get("symbol")
    if not symbol:
        raise ValueError("remove_function requires target.symbol")
        
    transformer = FunctionRemover(symbol)
    new_module = module.visit(transformer)
    if not transformer.removed:
        raise ValueError(f"Function {symbol} not found to remove")
    return new_module

def _apply_modify_class(module: cst.Module, target: dict, changes: dict) -> cst.Module:
    operation = changes.get("operation")
    payload = changes.get("payload", "")
    symbol = target.get("symbol")
    
    if not symbol:
        raise ValueError("modify_class requires target.symbol")
        
    if operation == "replace_body":
        indented_payload = textwrap.indent(payload.strip(), "    ")
        dummy_code = f"class Dummy:\n{indented_payload}"
        try:
            dummy_module = cst.parse_module(dummy_code)
            new_body = dummy_module.body[0].body # type: ignore
        except Exception as e:
            raise ValueError(f"Failed to parse new class body: {e}")
            
        transformer = ClassBodyReplacer(symbol, new_body)
        new_module = module.visit(transformer)
        if not transformer.found:
            raise ValueError(f"Class {symbol} not found in target file")
        return new_module
    else:
        raise NotImplementedError(f"Operation {operation} not fully implemented for modify_class")

def _apply_update_import(module: cst.Module, changes: dict) -> cst.Module:
    operation = changes.get("operation")
    imports = changes.get("imports", [])
    
    if operation == "add_import":
        parsed_imports = []
        for imp in imports:
            try:
                mod = cst.parse_module(imp)
                parsed_imports.extend(mod.body)
            except Exception as e:
                raise ValueError(f"Failed to parse import '{imp}': {e}")
                
        new_body = list(module.body)
        insert_idx = 0
        for i, stmt in enumerate(new_body):
            if isinstance(stmt, (cst.Import, cst.ImportFrom)):
                insert_idx = i + 1
                
        new_body = new_body[:insert_idx] + parsed_imports + new_body[insert_idx:]
        return module.with_changes(body=new_body)
    elif operation == "remove_import":
        raise NotImplementedError("remove_import not implemented")
    else:
        raise ValueError(f"Unsupported operation for update_import: {operation}")

def _apply_replace_block(module: cst.Module, changes: dict) -> cst.Module:
    operation = changes.get("operation")
    if operation != "context_replace":
        raise ValueError(f"Unsupported operation for replace_block: {operation}")
        
    context_before = changes.get("context_before", "")
    context_after = changes.get("context_after", "")
    payload = changes.get("payload", "")
    
    if not context_before:
        raise ValueError("replace_block requires context_before")
        
    source = module.code
    before_idx = source.find(context_before)
    if before_idx == -1:
        raise ValueError("context_before not found in source")
        
    if context_after:
        after_idx = source.find(context_after, before_idx + len(context_before))
        if after_idx == -1:
            raise ValueError("context_after not found in source")
        new_source = source[:before_idx + len(context_before)] + "\n" + payload + "\n" + source[after_idx:]
    else:
        new_source = source[:before_idx + len(context_before)] + "\n" + payload
        
    try:
        return cst.parse_module(new_source)
    except Exception as e:
        raise ValueError(f"Failed to parse file after replace_block: {e}")
