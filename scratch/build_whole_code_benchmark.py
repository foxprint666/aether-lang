import time
import uuid

# Aether's AST State Transition vs Traditional Token Generation
# Phase 3: Building a Whole Module Incrementally

def estimate_tokens(text: str) -> int:
    return len(text) // 4

def run_benchmark():
    print("=== STARTING BENCHMARK: Building a Whole Code ===")
    
    # Let's say we are building a 3-function module from scratch.
    
    func1 = """def initialize_system():
    print("System initialized")
    return True
"""
    func2 = """def connect_database(db_url):
    print(f"Connected to {db_url}")
    return "ConnectionObject"
"""
    func3 = """def start_server(port):
    print(f"Starting on port {port}")
    return True
"""

    # ----------------------------------------------------------------
    # METHOD A: Traditional Token Generation (Raw Text / File Rewrite)
    # ----------------------------------------------------------------
    # To build the code incrementally, the LLM has to re-generate the ENTIRE file each time 
    # it adds a function, to avoid diffing hallucinations.
    
    step1_out = func1
    step2_out = step1_out + "\n" + func2
    step3_out = step2_out + "\n" + func3
    
    tokens_a_step1 = estimate_tokens(step1_out)
    tokens_a_step2 = estimate_tokens(step2_out)
    tokens_a_step3 = estimate_tokens(step3_out)
    
    total_tokens_a = tokens_a_step1 + tokens_a_step2 + tokens_a_step3

    # ----------------------------------------------------------------
    # METHOD B: Aether AST State Transition (JSON Patch)
    # ----------------------------------------------------------------
    # The LLM generates an `add_function` patch for each step.
    
    patch1 = f'{{"action": "add_function", "target": {{"file": "app.py", "symbol": "initialize_system", "symbol_type": "function"}}, "changes": {{"operation": "insert_after", "payload": "{func1.strip()}"}}}}'
    patch2 = f'{{"action": "add_function", "target": {{"file": "app.py", "symbol": "connect_database", "symbol_type": "function"}}, "changes": {{"operation": "insert_after", "payload": "{func2.strip()}"}}}}'
    patch3 = f'{{"action": "add_function", "target": {{"file": "app.py", "symbol": "start_server", "symbol_type": "function"}}, "changes": {{"operation": "insert_after", "payload": "{func3.strip()}"}}}}'
    
    tokens_b_step1 = estimate_tokens(patch1)
    tokens_b_step2 = estimate_tokens(patch2)
    tokens_b_step3 = estimate_tokens(patch3)
    
    total_tokens_b = tokens_b_step1 + tokens_b_step2 + tokens_b_step3
    
    # ----------------------------------------------------------------
    # OUTPUT RESULTS
    # ----------------------------------------------------------------
    
    report = f"""
## Phase 3: Building a Whole Module (Incremental Construction)

When an AI agent builds an entire module incrementally (e.g., adding functions one by one), traditional approaches require the agent to constantly re-generate the entire file context to avoid corrupting the file structure. Aether allows the agent to build the file by stacking structured `add_function` AST patches.

**Scenario:** An agent builds a new module by adding 3 functions sequentially.

| Metric | Traditional Method (Re-generate full file) | Aether Method (Sequential `add_function` patches) | Improvement |
| :--- | :--- | :--- | :--- |
| **Step 1 Tokens** | {tokens_a_step1} tokens | {tokens_b_step1} tokens | |
| **Step 2 Tokens** | {tokens_a_step2} tokens (includes func1) | {tokens_b_step2} tokens (only func2 payload) | |
| **Step 3 Tokens** | {tokens_a_step3} tokens (includes func1+2)| {tokens_b_step3} tokens (only func3 payload) | |
| **Total Tokens Generated** | **{total_tokens_a} tokens** | **{total_tokens_b} tokens** | **{round((1 - total_tokens_b/total_tokens_a)*100)}% Reduction** |

### Why this matters for "Building Whole Code"
Even when writing a complete codebase from scratch, LLMs do it *iteratively* across multiple turns.
1. **Compounding Cost:** Traditional methods cause O(N^2) token generation costs as the file grows, because the entire file must be reprinted.
2. **Safety:** Aether ensures that each new block of code is a valid AST node before appending it, preventing a syntax error in Step 3 from destroying the work done in Steps 1 and 2.
"""
    
    print(report)
    
    # Append to benchmark_evidence.md
    with open("benchmark_evidence.md", "a") as f:
        f.write(report)
        
    print("Successfully appended to benchmark_evidence.md")

if __name__ == "__main__":
    run_benchmark()
