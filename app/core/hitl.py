import os
import json
import inspect
from functools import wraps
from google.adk.events.request_input import RequestInput
from google.adk.events.event import Event
from google.adk.agents.context import Context

def review_before_execute(api_action: str):
    """Decorator to enforce a Review-Before-Execute step. Pauses graph and creates a Verification Artifact."""
    def decorator(func):
        @wraps(func)
        async def wrapper(ctx: Context, *args, **kwargs):
            node_name = func.__name__
            interrupt_id = f"approve_{node_name}"

            # Check if approval is already submitted
            if not ctx.resume_inputs or interrupt_id not in ctx.resume_inputs:
                os.makedirs("verification_artifacts", exist_ok=True)
                artifact_path = f"verification_artifacts/{interrupt_id}.md"
                
                # Render Verification Artifact details
                details = {
                    "api_action": api_action,
                    "target_node": node_name,
                    "state_at_trigger": {k: str(v) for k, v in ctx.state.to_dict().items() if k != "leaves"}
                }
                with open(artifact_path, "w") as f:
                    f.write(f"# Verification Artifact: {node_name}\n\n")
                    f.write(f"- **API Action**: {api_action}\n")
                    f.write(f"- **Interrupt ID**: `{interrupt_id}`\n\n")
                    f.write("### State context:\n")
                    f.write(f"```json\n{json.dumps(details, indent=2)}\n```\n\n")
                    f.write("Please approve this action by resuming with: `{\"approved\": true}`.\n")

                yield RequestInput(
                    interrupt_id=interrupt_id,
                    message=f"[Review-Before-Execute] Approval required for '{api_action}' in node '{node_name}'. Verification Artifact created."
                )
                return

            # Handle resume response
            res = ctx.resume_inputs[interrupt_id]
            is_approved = True
            if isinstance(res, dict):
                is_approved = res.get("approved", True)
            elif isinstance(res, str):
                is_approved = "approve" in res.lower() or "yes" in res.lower() or res.strip() == ""

            if not is_approved:
                yield Event(output=f"Execution rejected by reviewer for node {node_name}.", state={"active_stage": f"{node_name}-Rejected"})
                return

            # Clean up verification artifact file
            artifact_path = f"verification_artifacts/{interrupt_id}.md"
            if os.path.exists(artifact_path):
                try:
                    os.remove(artifact_path)
                except Exception:
                    pass

            # Execute the actual node logic
            if inspect.iscoroutinefunction(func):
                res_val = await func(ctx, *args, **kwargs)
            else:
                res_val = func(ctx, *args, **kwargs)

            # If the result is a generator or async generator, yield from it
            if inspect.isasyncgen(res_val):
                async for item in res_val:
                    yield item
            elif inspect.isgenerator(res_val):
                for item in res_val:
                    yield item
            else:
                yield res_val

        return wrapper
    return decorator
