import pytest
import os
import json
import py_compile
from pathlib import Path
from ai_runtime.ast.engine import apply_patch
from ai_runtime.patch_engine import PatchEngine

def test_add_function(tmp_path: Path):
    target_file = tmp_path / "target.py"
    target_file.write_text("def existing():\n    pass\n", encoding="utf-8")
    
    patch = {
        "action": "add_function",
        "target": {
            "file": "target.py",
            "symbol": "new_func",
            "symbol_type": "function"
        },
        "changes": {
            "operation": "insert_after",
            "payload": "def new_func():\n    print('Hello World')\n"
        }
    }
    
    apply_patch(patch, str(tmp_path))
    content = target_file.read_text(encoding="utf-8")
    assert "def new_func():" in content
    assert "print('Hello World')" in content
    assert "def existing():" in content

def test_remove_function(tmp_path: Path):
    target_file = tmp_path / "target.py"
    target_file.write_text("def keep():\n    pass\n\ndef remove_me():\n    print('remove')\n", encoding="utf-8")
    
    patch = {
        "action": "remove_function",
        "target": {
            "file": "target.py",
            "symbol": "remove_me",
            "symbol_type": "function"
        },
        "changes": {
            "operation": "remove"
        }
    }
    
    apply_patch(patch, str(tmp_path))
    content = target_file.read_text(encoding="utf-8")
    assert "def keep():" in content
    assert "def remove_me():" not in content

def test_modify_function_replace_body(tmp_path: Path):
    target_file = tmp_path / "target.py"
    target_file.write_text("def modify_me():\n    old_logic()\n", encoding="utf-8")
    
    patch = {
        "action": "modify_function",
        "target": {
            "file": "target.py",
            "symbol": "modify_me",
            "symbol_type": "function"
        },
        "changes": {
            "operation": "replace_body",
            "payload": "print('new logic')\nreturn True\n"
        }
    }
    
    apply_patch(patch, str(tmp_path))
    content = target_file.read_text(encoding="utf-8")
    assert "old_logic()" not in content
    assert "print('new logic')" in content
    assert "return True" in content
    assert "def modify_me():" in content

def test_modify_function_replace_body_accepts_common_indentation(tmp_path: Path):
    target_file = tmp_path / "target.py"
    target_file.write_text("def modify_me():\n    return 0\n", encoding="utf-8")

    patch = {
        "action": "modify_function",
        "target": {
            "file": "target.py",
            "symbol": "modify_me",
            "symbol_type": "function",
        },
        "changes": {
            "operation": "replace_body",
            "payload": "    value = 41\n    return value + 1\n",
        },
    }

    apply_patch(patch, str(tmp_path))

    content = target_file.read_text(encoding="utf-8")
    assert "    value = 41" in content
    assert "    return value + 1" in content

def test_update_import_add(tmp_path: Path):
    target_file = tmp_path / "target.py"
    target_file.write_text("import sys\n\ndef my_func():\n    pass\n", encoding="utf-8")
    
    patch = {
        "action": "update_import",
        "target": {
            "file": "target.py"
        },
        "changes": {
            "operation": "add_import",
            "imports": ["import os", "from typing import List"]
        }
    }
    
    apply_patch(patch, str(tmp_path))
    content = target_file.read_text(encoding="utf-8")
    assert "import os" in content
    assert "from typing import List" in content
    assert "import sys" in content
    
    # Imports should typically appear before my_func (index checks could be more precise but this is a start)
    assert content.index("import os") < content.index("def my_func():")

def test_update_import_preserves_future_import_position(tmp_path: Path):
    target_file = tmp_path / "target.py"
    target_file.write_text(
        '"""Module docs."""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import sys\n"
        "\n"
        "def my_func() -> str:\n"
        "    return 'ok'\n",
        encoding="utf-8",
    )

    patch = {
        "action": "update_import",
        "target": {
            "file": "target.py"
        },
        "changes": {
            "operation": "add_import",
            "imports": ["import tempfile"]
        }
    }

    apply_patch(patch, str(tmp_path))
    content = target_file.read_text(encoding="utf-8")
    assert content.index('"""Module docs."""') < content.index("from __future__ import annotations")
    assert content.index("from __future__ import annotations") < content.index("import tempfile")
    assert content.index("import tempfile") < content.index("def my_func()")
    py_compile.compile(str(target_file), doraise=True)

def test_patch_engine_integration(tmp_path: Path):
    target_file = tmp_path / "target.py"
    target_file.write_text("def old():\n    pass\n", encoding="utf-8")
    
    engine = PatchEngine(project_root=str(tmp_path))
    patch = {
        "schema_version": "1.0",
        "patch_id": "123e4567-e89b-42d3-a456-426614174000",
        "action": "add_function",
        "target": {
            "file": "target.py",
            "symbol": "new",
            "symbol_type": "function"
        },
        "changes": {
            "operation": "replace_body",
            "payload": "def new():\n    return 42\n"
        }
    }
    
    report = engine.process(patch)
    assert report.ok
    
    content = target_file.read_text(encoding="utf-8")
    assert "def new():\n    return 42" in content

def test_modify_class_replace_body(tmp_path: Path):
    target_file = tmp_path / "target.py"
    target_file.write_text("class MyClass:\n    def old_method(self):\n        pass\n", encoding="utf-8")
    
    patch = {
        "action": "modify_class",
        "target": {
            "file": "target.py",
            "symbol": "MyClass",
            "symbol_type": "class"
        },
        "changes": {
            "operation": "replace_body",
            "payload": "def new_method(self):\n    return 42\n"
        }
    }
    
    apply_patch(patch, str(tmp_path))
    content = target_file.read_text(encoding="utf-8")
    assert "old_method" not in content
    assert "def new_method(self):" in content
    assert "class MyClass:" in content

def test_replace_block(tmp_path: Path):
    target_file = tmp_path / "target.py"
    target_file.write_text(
        "def some_func():\n"
        "    # A comment\n"
        "    x = 1\n"
        "    y = 2\n"
        "    z = 3\n"
        "    return x + y + z\n", encoding="utf-8")
    
    patch = {
        "action": "replace_block",
        "target": {
            "file": "target.py"
        },
        "changes": {
            "operation": "context_replace",
            "context_before": "    x = 1\n",
            "context_after": "    z = 3\n",
            "payload": "    y = 42"
        }
    }
    
    apply_patch(patch, str(tmp_path))
    content = target_file.read_text(encoding="utf-8")
    assert "x = 1" in content
    assert "y = 2" not in content
    assert "y = 42" in content
    assert "z = 3" in content
    assert "# A comment" in content


def test_replace_block_rejects_missing_context_after(tmp_path: Path):
    target_file = tmp_path / "target.py"
    target_file.write_text("def f():\n    x = 1\n    y = 2\n", encoding="utf-8")

    patch = {
        "action": "replace_block",
        "target": {
            "file": "target.py",
        },
        "changes": {
            "operation": "context_replace",
            "context_before": "    x = 1\n",
            "payload": "    y = 42",
        },
    }

    with pytest.raises(ValueError, match="context_after"):
        apply_patch(patch, str(tmp_path))

    assert target_file.read_text(encoding="utf-8") == "def f():\n    x = 1\n    y = 2\n"


def test_replace_block_rejects_ambiguous_context_before(tmp_path: Path):
    target_file = tmp_path / "target.py"
    target_file.write_text(
        "def f():\n"
        "    x = 1\n"
        "    y = 2\n"
        "    x = 1\n"
        "    z = 3\n",
        encoding="utf-8",
    )

    patch = {
        "action": "replace_block",
        "target": {
            "file": "target.py",
        },
        "changes": {
            "operation": "context_replace",
            "context_before": "    x = 1\n",
            "context_after": "    z = 3\n",
            "payload": "    y = 42",
        },
    }

    with pytest.raises(ValueError, match="ambiguous"):
        apply_patch(patch, str(tmp_path))

    assert "y = 2" in target_file.read_text(encoding="utf-8")
