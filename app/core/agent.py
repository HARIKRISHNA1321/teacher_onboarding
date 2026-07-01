import os
import google.auth
from pydantic import BaseModel, Field
from typing import List, Dict, Any

from google.adk.apps import App, ResumabilityConfig
from google.adk.agents import LlmAgent
from google.adk.agents.context import Context
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.workflow import Workflow, JoinNode, node, START

from app.core.local_storage import LocalStateStore
from app.core.hitl import review_before_execute
from app.core.privacy import DataMaskingMiddleware
from app.tools.pinecone_rag import PineconeRAGService

# Set up environment variables for authentication
try:
    _, project_id = google.auth.default()
except Exception:
    project_id = "mock-project-id"
os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

# --- 1. Global State Management Schema ---
class WorkflowState(BaseModel):
    candidate_name: str = "Dr. Jane Doe"
    active_stage: str = "START"
    confirmation_email_sent: bool = False
    credentials_sent: bool = False
    documents: List[str] = Field(default_factory=list)
    policy_brief: str = ""
    manager_interview_scheduled: bool = False
    chairperson_notified: bool = False
    final_approval_flag: bool = False
    allotment_criteria: Dict[str, Any] = Field(default_factory=dict)
    it_notified: bool = False
    admin_notified: bool = False
    leave_balance: int = 30
    leaves: List[Dict[str, Any]] = Field(default_factory=list)

# --- 2. Workflow Routing and Node Implementations ---

def router_node(ctx: Context, node_input: Any) -> Event:
    """Classifies input queries to route between chatbot and onboarding pipeline."""
    # Sync from local storage schema if it exists
    local_store = LocalStateStore()
    stored_state = local_store.load_state()
    if stored_state:
        for k, v in stored_state.items():
            ctx.state[k] = v

    text = str(node_input)
    if any(keyword in text.lower() for keyword in ["leave", "apply", "balance", "policy", "days"]):
        return Event(output=node_input, route="chatbot")
    return Event(output=node_input, route="onboarding")


def chatbot_node(ctx: Context, node_input: Any) -> Event:
    """On-Demand parallel chatbot verifying leave database & policy rules."""
    # Central Data Masking Layer: Scrub PII from input before evaluating
    clean_input = DataMaskingMiddleware.redact_pii(str(node_input))
    
    response = ""
    state_updates = {}

    if "leave" in clean_input.lower() or "apply" in clean_input.lower():
        import re
        days_match = re.search(r"(\d+)\s*day", clean_input.lower())
        days = int(days_match.group(1)) if days_match else 1
        
        balance = ctx.state.get("leave_balance", 30)
        if balance >= days:
            new_balance = balance - days
            leaves = ctx.state.get("leaves", [])
            leaves.append({"days": days, "status": "approved", "request": clean_input})
            state_updates["leave_balance"] = new_balance
            state_updates["leaves"] = leaves
            response = f"Success: Leave of {days} days approved. Remaining leave balance: {new_balance} days."
        else:
            response = f"Failed: Insufficient leave balance. Requested {days} days but you only have {balance} days."
    else:
        # Vector search query simulation over Pinecone using the masked input
        pinecone_service = PineconeRAGService()
        response = pinecone_service.query_rules(clean_input)

    # Sync to local storage
    local_store = LocalStateStore()
    current_state = local_store.load_state()
    current_state.update(state_updates)
    local_store.save_state(current_state)

    return Event(output=response, state=state_updates)


@review_before_execute(api_action="Email HR & Candidate Interview Confirmation")
def initial_interview(ctx: Context, node_input: Any) -> Event:
    """Processes post-interview status and triggers confirmation event."""
    state_updates = {
        "confirmation_email_sent": True,
        "active_stage": "Initial-Interview-Passed"
    }
    local_store = LocalStateStore()
    current_state = local_store.load_state()
    current_state.update(state_updates)
    local_store.save_state(current_state)

    msg = "Initial Interview Complete. Confirmation email fired to HR & Candidate."
    return Event(output=msg, state=state_updates)


def triggered_procedures(ctx: Context, node_input: Any) -> Event:
    """Acts as a state gate to programmatically initiate subsequent tasks."""
    confirmation = ctx.state.get("confirmation_email_sent", False)
    if confirmation:
        state_updates = {"active_stage": "Procedures-Initiated"}
        local_store = LocalStateStore()
        current_state = local_store.load_state()
        current_state.update(state_updates)
        local_store.save_state(current_state)
        return Event(output="Confirmed: Initiating credentials, onboarding and scheduling tasks.", route="start_procedures", state=state_updates)
    return Event(output="Procedures halted: Confirmation email flag is False.", route="halted")


@review_before_execute(api_action="Generate & dispatch secure portal credentials via SMTP")
def credential_agent(ctx: Context, node_input: Any) -> Event:
    """Automatically generates and emails portal credentials."""
    state_updates = {
        "credentials_sent": True,
        "active_stage": "Credentials-Generated"
    }
    local_store = LocalStateStore()
    current_state = local_store.load_state()
    current_state.update(state_updates)
    local_store.save_state(current_state)

    msg = "Credentials Generated: Portal username: jane.doe@pes.edu has been sent safely."
    return Event(output=msg, state=state_updates)


async def onboarding_guide(ctx: Context, node_input: Any):
    """Guides the teacher through the scan-and-upload process for joining documents."""
    if not ctx.resume_inputs or "uploaded_documents" not in ctx.resume_inputs:
        yield RequestInput(
            interrupt_id="uploaded_documents",
            message="Onboarding Guide: Please upload your scanned joining letters and structural documents (comma-separated):"
        )
        return

    res = ctx.resume_inputs["uploaded_documents"]
    if isinstance(res, dict):
        res_val = res.get("uploaded_documents") or res.get("result") or list(res.values())[0]
    else:
        res_val = res

    docs = [d.strip() for d in str(res_val).split(",")]
    state_updates = {
        "documents": docs,
        "active_stage": "Documents-Uploaded"
    }
    local_store = LocalStateStore()
    current_state = local_store.load_state()
    current_state.update(state_updates)
    local_store.save_state(current_state)

    yield Event(output=f"Onboarding Guide: Documents received: {docs}", state=state_updates)


def policy_rag_agent(ctx: Context, node_input: Any) -> Event:
    """Uses a simulated Llama 3.1 LLM response to check file formats and output college rules brief."""
    if isinstance(node_input, dict):
        res_val = node_input.get("uploaded_documents") or node_input.get("result") or list(node_input.values())[0]
    else:
        res_val = node_input

    # Privacy Scrubbing: mask potential PII in document contents/filenames before rules lookup
    clean_val = DataMaskingMiddleware.redact_pii(str(res_val))
    docs = [d.strip() for d in clean_val.split(",") if d.strip()]
    
    verified_files = [f for f in docs if f.endswith(('.pdf', '.docx'))]
    
    # Query production Pinecone vector database
    pinecone_service = PineconeRAGService()
    brief = pinecone_service.query_rules(clean_val)

    state_updates = {
        "documents": docs,
        "policy_brief": brief,
        "active_stage": "Policy-Checked"
    }
    local_store = LocalStateStore()
    current_state = local_store.load_state()
    current_state.update(state_updates)
    local_store.save_state(current_state)

    msg = f"Policy RAG (Llama 3.1 Simulation) complete. Verified: {verified_files}.\n{brief}"
    return Event(output=msg, state=state_updates)


@review_before_execute(api_action="Schedule calendar appointment and invite chairperson")
def scheduler_agent(ctx: Context, node_input: Any) -> Event:
    """Manages sequential scheduling: first with manager, then email chairperson."""
    if not ctx.state.get("manager_interview_scheduled", False):
        state_updates = {
            "manager_interview_scheduled": True,
            "active_stage": "Manager-Interview-Scheduled"
        }
        local_store = LocalStateStore()
        current_state = local_store.load_state()
        current_state.update(state_updates)
        local_store.save_state(current_state)
        return Event(output="Scheduler: Manager interview scheduled.", route="email_chairperson", state=state_updates)
    
    state_updates = {
        "chairperson_notified": True,
        "active_stage": "Chairperson-Notified"
    }
    local_store = LocalStateStore()
    current_state = local_store.load_state()
    current_state.update(state_updates)
    local_store.save_state(current_state)
    return Event(output="Scheduler: Chairperson emailed to secure final presentation availability.", route="final_presentation_secured", state=state_updates)


async def allotment_approval_gate(ctx: Context, node_input: Any):
    """Listens for final approval and requests place and seat allotment criteria."""
    if not ctx.state.get("final_approval_flag", False):
        if not ctx.resume_inputs or "allotment_criteria" not in ctx.resume_inputs:
            yield RequestInput(
                interrupt_id="allotment_criteria",
                message="Allotment Approval Gate: Submit place and seat allotment criteria (e.g. Room 401, Desk B):"
            )
            return

        res = ctx.resume_inputs["allotment_criteria"]
        if isinstance(res, dict):
            res_val = res.get("allotment_criteria") or res.get("result") or list(res.values())[0]
        else:
            res_val = res

        criteria = str(res_val)
        state_updates = {
            "allotment_criteria": {"criteria": criteria},
            "final_approval_flag": True,
            "active_stage": "Seat-Allotted"
        }
        local_store = LocalStateStore()
        current_state = local_store.load_state()
        current_state.update(state_updates)
        local_store.save_state(current_state)

        yield Event(output=f"Allotment Gate: Seat approved with criteria: {criteria}", state=state_updates)
    else:
        yield Event(output="Allotment already approved.")


@review_before_execute(api_action="Notify IT and Administrative departments for campus provisioning")
def follow_up_provisioning(ctx: Context, node_input: Any) -> Event:
    """Blasts templates to IT & Admin for Wi-Fi, email, and ID printing."""
    state_updates = {
        "it_notified": True,
        "admin_notified": True,
        "active_stage": "Provisioning-Done"
    }
    local_store = LocalStateStore()
    current_state = local_store.load_state()
    current_state.update(state_updates)
    local_store.save_state(current_state)

    msg = (
        "Follow-Up Provisioning Complete:\n"
        "- IT notified for physical ID printing & Campus Wi-Fi.\n"
        "- Admin notified for official pes.edu email creation."
    )
    return Event(output=msg, state=state_updates)


# --- 3. Graph Topology Definitions ---

join_procedures = JoinNode(name="join_procedures")

edges_definition = [
    # Router entry point
    (START, router_node),
    
    # Conditional routes from router_node
    (router_node, {"chatbot": chatbot_node, "onboarding": initial_interview}),
    
    # Onboarding main flow
    (initial_interview, triggered_procedures),
    (triggered_procedures, {"start_procedures": (credential_agent, onboarding_guide, scheduler_agent)}),
    
    # Onboarding Guide -> Policy check flow
    (onboarding_guide, policy_rag_agent),
    
    # Scheduler routes (self loop and fanning in to join)
    (scheduler_agent, {"email_chairperson": scheduler_agent, "final_presentation_secured": join_procedures}),
    
    # Join paths
    ((credential_agent, policy_rag_agent), join_procedures),
    
    # Allotment Gate & provisioning post-join
    (join_procedures, allotment_approval_gate),
    (allotment_approval_gate, follow_up_provisioning)
]

state_manager_agent = Workflow(
    name="state_manager_agent",
    state_schema=WorkflowState,
    edges=edges_definition
)

app = App(
    root_agent=state_manager_agent,
    name="app",
    resumability_config=ResumabilityConfig(enabled=True)
)
