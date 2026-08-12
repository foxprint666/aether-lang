import os
import json
import tiktoken
from pathlib import Path

enc = tiktoken.get_encoding("cl100k_base")

def count_tokens(text: str) -> int:
    return len(enc.encode(text))

def main():
    target_file = Path(r"c:\Users\ASHLEY ALLEN\Downloads\aether-lang\sdk\python\ai_runtime\sandbox_t3.py")
    code_content = target_file.read_text(encoding="utf-8")
    
    live_patch_file = Path(r"c:\Users\ASHLEY ALLEN\Downloads\aether-lang\scratch\live_patch.json")
    patch_content = live_patch_file.read_text(encoding="utf-8")
    
    full_rewrite_tokens = count_tokens(code_content)
    patch_tokens = count_tokens(patch_content)
    savings_pct = (1.0 - (patch_tokens / full_rewrite_tokens)) * 100
    
    print(f"Full File Tokens: {full_rewrite_tokens}")
    print(f"Live Patch Tokens: {patch_tokens}")
    print(f"Reduction: {savings_pct:.2f}%")

if __name__ == "__main__":
    main()
