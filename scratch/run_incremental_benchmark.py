import json
import tiktoken

enc = tiktoken.get_encoding("cl100k_base")

def count_tokens(text: str) -> int:
    return len(enc.encode(text))

def main():
    # Simulate an AI building an enterprise module with 10 realistic functions sequentially.
    # Each function is about 15-20 lines of code (~100-150 tokens).
    
    dummy_function_template = """def enterprise_function_{i}(data: dict, config: dict) -> dict:
    \"\"\"
    Processes the enterprise data based on the provided configuration.
    Includes validation, logging, and error handling.
    \"\"\"
    if not data:
        raise ValueError("Data cannot be empty")
        
    result = {{}}
    for key, value in data.items():
        if key in config.get("allowed_keys", []):
            try:
                # Perform complex transformation
                transformed = str(value).upper() + "_PROCESSED"
                result[key] = transformed
            except Exception as e:
                # Log error and continue
                print(f"Error processing {{key}}: {{e}}")
                continue
                
    return result
"""
    
    functions = [dummy_function_template.format(i=i) for i in range(10)]
    
    # 1. Traditional Method: LLM rewrites the entire file each turn
    traditional_total_tokens = 0
    current_file_content = ""
    
    # 2. Aether Method: LLM outputs an add_function JSON patch each turn
    aether_total_tokens = 0
    
    for i, func in enumerate(functions):
        # Traditional Step
        current_file_content += "\n" + func
        step_traditional_tokens = count_tokens(current_file_content.strip())
        traditional_total_tokens += step_traditional_tokens
        
        # Aether Step
        patch = {
            "patch_id": f"patch-{i}",
            "action": "add_function",
            "target": {"file": "new_enterprise_module.py"},
            "content": func.strip()
        }
        patch_json = json.dumps(patch, indent=2)
        step_aether_tokens = count_tokens(patch_json)
        aether_total_tokens += step_aether_tokens
        
    reduction = (1.0 - (aether_total_tokens / traditional_total_tokens)) * 100
    
    print("=== INCREMENTAL MODULE CREATION (10 STEPS) ===")
    print(f"Traditional Tokens (Compounding): {traditional_total_tokens:,}")
    print(f"Aether Tokens (Flat Patches): {aether_total_tokens:,}")
    print(f"Token Reduction: {reduction:.2f}%")
    
if __name__ == "__main__":
    main()
