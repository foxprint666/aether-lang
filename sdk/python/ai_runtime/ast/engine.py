"""
ai_runtime.ast.engine
~~~~~~~~~~~~~~~~~~~~~
AST-based application engine using libcst to preserve formatting and comments.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
import libcst as cst
from libcst.metadata import PositionProvider

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
    wrapper = cst.MetadataWrapper(module)
    
    # Dispatch
    if action == "modify_function":
        module = _apply_modify_symbol(wrapper, target, patch.get("changes", {}), "function")
    elif action == "add_function":
        module = _apply_add_function(module, target, patch.get("changes", {}))
    elif action == "remove_function":
        module = _apply_remove_symbol(wrapper, target, "function")
    elif action == "modify_class":
        module = _apply_modify_symbol(wrapper, target, patch.get("changes", {}), "class")
    elif action == "update_import":
        module = _apply_update_import(module, patch.get("changes", {}))
    elif action == "replace_block":
        module = _apply_replace_block(module, patch.get("changes", {}))
    else:
        raise ValueError(f"Unsupported action: {action}")
        
    # Write back
    file_path.write_text(module.code, encoding="utf-8")


class SymbolModifier(cst.CSTTransformer):
    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, symbol_name: str, symbol_type: str, operation: str, payload: str):
        self.symbol_name = symbol_name
        self.symbol_type = symbol_type
        self.operation = operation
        self.payload = textwrap.dedent(payload).strip()
        self.found = False

    def _handle_node(self, original_node, updated_node):
        if original_node.name.value != self.symbol_name:
            return updated_node

        self.found = True
        pos = self.get_metadata(PositionProvider, original_node)
        indent = " " * pos.start.column

        if self.operation in ("replace_body", "update_logic"):
            indented_payload = textwrap.indent(self.payload, indent + "    ")
            # Create a dummy container based on type
            if isinstance(original_node, cst.ClassDef):
                dummy_code = f"class Dummy:\n{indented_payload}"
            else:
                dummy_code = f"def dummy():\n{indented_payload}"
            
            try:
                dummy_module = cst.parse_module(dummy_code)
                new_body = dummy_module.body[0].body
                return updated_node.with_changes(body=new_body)
            except Exception as e:
                raise ValueError(f"Failed to parse new body for {self.symbol_name}: {e}")

        elif self.operation in ("insert_before", "insert_after"):
            indented_payload = textwrap.indent(self.payload, indent)
            try:
                dummy_module = cst.parse_module(indented_payload)
                new_nodes = dummy_module.body
            except Exception as e:
                raise ValueError(f"Failed to parse payload for {self.symbol_name}: {e}")
            
            if self.operation == "insert_before":
                return cst.FlattenSentinel([*new_nodes, updated_node])
            else:
                return cst.FlattenSentinel([updated_node, *new_nodes])

        return updated_node

    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef):
        if self.symbol_type == "function":
            return self._handle_node(original_node, updated_node)
        return updated_node

    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef):
        if self.symbol_type == "class":
            return self._handle_node(original_node, updated_node)
        return updated_node


class SymbolRemover(cst.CSTTransformer):
    def __init__(self, target_name: str, symbol_type: str):
        self.target_name = target_name
        self.symbol_type = symbol_type
        self.removed = False

    def _handle_node(self, original_node, updated_node):
        if original_node.name.value == self.target_name:
            self.removed = True
            return cst.RemoveFromParent()
        return updated_node

    def leave_FunctionDef(self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef):
        if self.symbol_type == "function":
            return self._handle_node(original_node, updated_node)
        return updated_node

    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef):
        if self.symbol_type == "class":
            return self._handle_node(original_node, updated_node)
        return updated_node


class ImportRemover(cst.CSTTransformer):
    def __init__(self, imports_to_remove: list[str]):
        self.target_strings = set(imp.strip() for imp in imports_to_remove)

    def leave_Import(self, original_node: cst.Import, updated_node: cst.Import):
        code = cst.Module(body=[original_node]).code.strip()
        if code in self.target_strings:
            return cst.RemoveFromParent()
        return updated_node

    def leave_ImportFrom(self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom):
        code = cst.Module(body=[original_node]).code.strip()
        if code in self.target_strings:
            return cst.RemoveFromParent()
        return updated_node


def _apply_modify_symbol(wrapper: cst.MetadataWrapper, target: dict, changes: dict, symbol_type: str) -> cst.Module:
    operation = changes.get("operation")
    payload = changes.get("payload", "")
    symbol = target.get("symbol")
    
    if not symbol:
        raise ValueError(f"modify_{symbol_type} requires target.symbol")
        
    transformer = SymbolModifier(symbol, symbol_type, operation, payload)
    new_module = wrapper.visit(transformer)
    if not transformer.found:
        raise ValueError(f"{symbol_type.capitalize()} {symbol} not found in target file")
    return new_module

def _apply_remove_symbol(wrapper: cst.MetadataWrapper, target: dict, symbol_type: str) -> cst.Module:
    symbol = target.get("symbol")
    if not symbol:
        raise ValueError(f"remove_{symbol_type} requires target.symbol")
        
    transformer = SymbolRemover(symbol, symbol_type)
    new_module = wrapper.visit(transformer)
    if not transformer.removed:
        raise ValueError(f"{symbol_type.capitalize()} {symbol} not found to remove")
    return new_module

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
        insert_idx = _import_insert_index(new_body)
                
        new_body = new_body[:insert_idx] + parsed_imports + new_body[insert_idx:]
        return module.with_changes(body=new_body)
    elif operation == "remove_import":
        transformer = ImportRemover(imports)
        return module.visit(transformer)
    else:
        raise ValueError(f"Unsupported operation for update_import: {operation}")


def _import_insert_index(body: list[cst.CSTNode]) -> int:
    insert_idx = 0
    saw_import_block = False

    for i, stmt in enumerate(body):
        first = _first_small_statement(stmt)
        if first is None:
            if insert_idx == 0 and _is_module_docstring(stmt):
                insert_idx = i + 1
                continue
            if saw_import_block:
                break
            continue

        if _is_future_import(first):
            insert_idx = i + 1
            continue

        if isinstance(first, (cst.Import, cst.ImportFrom)):
            insert_idx = i + 1
            saw_import_block = True
            continue

        if saw_import_block:
            break

    return insert_idx


def _first_small_statement(stmt: cst.CSTNode) -> cst.CSTNode | None:
    if not isinstance(stmt, cst.SimpleStatementLine) or not stmt.body:
        return None
    return stmt.body[0]


def _is_future_import(stmt: cst.CSTNode) -> bool:
    return (
        isinstance(stmt, cst.ImportFrom)
        and isinstance(stmt.module, cst.Name)
        and stmt.module.value == "__future__"
    )


def _is_module_docstring(stmt: cst.CSTNode) -> bool:
    first = _first_small_statement(stmt)
    return (
        isinstance(first, cst.Expr)
        and isinstance(first.value, cst.SimpleString)
    )

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
