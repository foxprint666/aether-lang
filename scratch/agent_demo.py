import uuid
import sys
import os

# Add SDK to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sdk", "python")))

from ai_runtime import PatchOrchestrator

patch_payload = """def process_refund(self, tx: Transaction) -> bool:
    \"\"\"Process a refund for a transaction.\"\"\"
    if tx.status != "COMPLETED":
        logger.error(f"Cannot refund incomplete tx {tx.tx_id}")
        return False
    
    try:
        self._execute_network_call(tx)
        tx.status = "REFUNDED"
        return True
    except Exception as e:
        logger.error(f"Refund failed for {tx.tx_id}: {e}")
        return False"""

patch = {
    "schema_version": "1.0",
    "patch_id": str(uuid.uuid4()),
    "action": "add_function",
    "target": {
        "file": "demo_app/processor.py",
        "symbol": "PaymentProcessor",
        "symbol_type": "class"
    },
    "changes": {
        "operation": "replace_body", # Wait, for add_function, operation might need to be something else... let's look at patch_engine.py
        "payload": patch_payload
    }
}

orchestrator = PatchOrchestrator(project_root=".")
result = orchestrator.apply(patch)

print(f"Orchestrator Result: {result.ok}")
if not result.ok:
    print(f"Errors: {result.errors}")
else:
    print("Successfully injected process_refund via Aether AST State Transition!")
