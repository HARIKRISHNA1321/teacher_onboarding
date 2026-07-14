# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import re
import sys
import json
import uuid
import secrets
import asyncio
import logging
import datetime
import threading
import time
from typing import Dict, Any, List, Optional, Iterator
from functools import wraps
import inspect

import requests
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from pydantic import BaseModel, Field
import google.auth
from fastapi import FastAPI, APIRouter, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
import streamlit as st

# google-adk imports
from google.adk.apps import App, ResumabilityConfig
from google.adk.agents import LlmAgent
from google.adk.agents.context import Context
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.workflow import Workflow, JoinNode, node, START
from google.adk.cli.fast_api import get_fast_api_app
from google.cloud import logging as google_cloud_logging

# =====================================================================
# 1. APPLICATION CONFIGURATION (formerly app/core/config.py)
# =====================================================================
from dotenv import load_dotenv
load_dotenv(override=True)

PORT = int(os.getenv("PORT", "8000"))
HOST = os.getenv("HOST", "0.0.0.0")
ENV = os.getenv("ENV", "production")

PINECONE_API_KEY = os.getenv("PINECONE_API_KEY", "")
PINECONE_ENV = os.getenv("PINECONE_ENV", "")

HR_EMAIL = os.getenv("HR_EMAIL", "hr@pes.edu")
SMTP_TOKEN = os.getenv("SMTP_TOKEN", "mock-smtp-token")
CALENDAR_TOKEN = os.getenv("CALENDAR_TOKEN", "mock-calendar-token")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "hr@pes.edu")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "mock-password")


# =====================================================================
# 2. TELEMETRY CONFIGURATION (formerly app/app_utils/telemetry.py)
# =====================================================================
def setup_telemetry() -> str | None:
    """Configure OpenTelemetry and GenAI telemetry with GCS upload."""
    bucket = os.environ.get("LOGS_BUCKET_NAME")
    capture_content = os.environ.get(
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "false"
    )
    if bucket and capture_content != "false":
        logging.info(
            "Prompt-response logging enabled - mode: NO_CONTENT (metadata only, no prompts/responses)"
        )
        os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "NO_CONTENT"
        os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_UPLOAD_FORMAT", "jsonl")
        os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_COMPLETION_HOOK", "upload")
        os.environ.setdefault(
            "OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental"
        )
        commit_sha = os.environ.get("COMMIT_SHA", "dev")
        os.environ.setdefault(
            "OTEL_RESOURCE_ATTRIBUTES",
            f"service.namespace=college-onboard-platform,service.version={commit_sha}",
        )
        path = os.environ.get("GENAI_TELEMETRY_PATH", "completions")
        os.environ.setdefault(
            "OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH",
            f"gs://{bucket}/{path}",
        )
    else:
        logging.info(
            "Prompt-response logging disabled (set LOGS_BUCKET_NAME=gs://your-bucket and OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT to enable)"
        )
    return bucket


# =====================================================================
# 3. PRIVACY & SECURITY MIDDLEWARE (formerly app/core/privacy.py)
# =====================================================================
class DataMaskingMiddleware:
    EMAIL_REGEX = re.compile(r'[\w\.-]+@[\w\.-]+\.\w+')
    CREDENTIAL_REGEX = re.compile(r'(password|token|key|secret)\s*[:=]\s*[\'"]?[\w\-\.\!\@\#\$\%\^\&\*]+[\'"]?', re.IGNORECASE)

    @classmethod
    def redact_pii(cls, val: Any) -> Any:
        if isinstance(val, str):
            val = cls.EMAIL_REGEX.sub("[EMAIL]", val)
            val = cls.CREDENTIAL_REGEX.sub(r"\1: [REDACTED_CREDENTIAL]", val)
            return val
        elif isinstance(val, dict):
            return {k: cls.redact_pii(v) for k, v in val.items()}
        elif isinstance(val, list):
            return [cls.redact_pii(x) for x in val]
        return val

class SecretManager:
    """Secures credentials, preventing hardcoded keys or leaking secrets in logs/traces."""
    @staticmethod
    def get_secret(name: str, default: str = "") -> str:
        return os.getenv(name, default)


# =====================================================================
# 4. LOCAL DATABASE STATE STORE (formerly app/core/local_storage.py)
# =====================================================================
class LocalStateStore:
    def __init__(self, filepath="state_store.json"):
        self.filepath = filepath

    def load_state(self) -> Dict[str, Any]:
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save_state(self, state_dict: Dict[str, Any]):
        with open(self.filepath, "w") as f:
            json.dump(state_dict, f, indent=2)

    def update_field(self, key: str, value: Any):
        state = self.load_state()
        state[key] = value
        self.save_state(state)


# =====================================================================
# 5. PINECONE VECTOR DB SERVICE (formerly app/tools/pinecone_rag.py)
# =====================================================================
class PineconeRAGService:
    def __init__(self):
        load_dotenv(override=True)
        self.api_key = os.getenv("PINECONE_API_KEY", "")
        self.env = os.getenv("PINECONE_ENV", "")
        self.gemini_key = os.getenv("GEMINI_API_KEY", "")

    def query_rules(self, document_content: str) -> str:
        scrubbed = DataMaskingMiddleware.redact_pii(document_content)
        if not self.api_key or not self.gemini_key:
            return self.get_fallback_brief(scrubbed)

        try:
            from pinecone import Pinecone
            # 1. Get query embedding from Gemini
            embed_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2:embedContent?key={self.gemini_key}"
            headers = {"Content-Type": "application/json"}
            data = {
                "model": "models/gemini-embedding-2",
                "content": {"parts": [{"text": scrubbed}]},
                "outputDimensionality": 3072
            }
            res = requests.post(embed_url, headers=headers, json=data, timeout=15.0)
            if res.status_code == 200:
                vector = res.json()["embedding"]["values"]
                
                # 2. Connect to Pinecone and Query index
                pc = Pinecone(api_key=self.api_key)
                index_name = "gemini-rag-3072"
                idx = pc.Index(index_name)
                
                query_res = idx.query(vector=vector, top_k=3, include_metadata=True)
                
                # 3. Parse and format retrieved text chunks
                context_pieces = []
                for match in query_res.matches:
                    if match.metadata and "text" in match.metadata:
                        context_pieces.append(f"- {match.metadata['text'].strip()}")
                
                if context_pieces:
                    return f"[Pinecone Index: {index_name}] RETRIEVED REAL-TIME RULES:\n" + "\n".join(context_pieces)
        except Exception:
            pass

        return self.get_fallback_brief(scrubbed)

    def get_fallback_brief(self, scrubbed: str) -> str:
        return (
            f"[Pinecone Search (Simulation)] RETRIEVED RULES CONTEXT:\n"
            f"- Data Input (PII Scrubbed): {scrubbed}\n"
            "- Joining guidelines: Submit original verification documents within 30 days.\n"
            "- Campus ethics: Absolute professionalism in research and teaching duties."
        )


# =====================================================================
# 6. HITL DECORATOR (formerly app/core/hitl.py)
# =====================================================================
def review_before_execute(api_action: str):
    """Decorator to enforce a Review-Before-Execute step. Pauses graph and creates a Verification Artifact."""
    def decorator(func):
        @wraps(func)
        async def wrapper(ctx: Context, *args, **kwargs):
            node_name = func.__name__
            interrupt_id = f"approve_{node_name}"

            if not ctx.resume_inputs or interrupt_id not in ctx.resume_inputs:
                os.makedirs("verification_artifacts", exist_ok=True)
                artifact_path = f"verification_artifacts/{interrupt_id}.md"
                
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

            res = ctx.resume_inputs[interrupt_id]
            is_approved = True
            if isinstance(res, dict):
                is_approved = res.get("approved", True)
            elif isinstance(res, str):
                is_approved = "approve" in res.lower() or "yes" in res.lower() or res.strip() == ""

            if not is_approved:
                yield Event(output=f"Execution rejected by reviewer for node {node_name}.", state={"active_stage": f"{node_name}-Rejected"})
                return

            artifact_path = f"verification_artifacts/{interrupt_id}.md"
            if os.path.exists(artifact_path):
                try:
                    os.remove(artifact_path)
                except Exception:
                    pass

            if inspect.iscoroutinefunction(func):
                res_val = await func(ctx, *args, **kwargs)
            else:
                res_val = func(ctx, *args, **kwargs)

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


# =====================================================================
# 7. ADK WORKFLOW SCHEMAS & TOPOLOGY (formerly app/core/agent.py)
# =====================================================================
project_id_auth = "mock-project-id"
if not st.runtime.exists():
    try:
        _, project_id_auth = google.auth.default()
    except Exception:
        project_id_auth = None

if not project_id_auth:
    project_id_auth = os.environ.get("GOOGLE_CLOUD_PROJECT") or "mock-project-id"


os.environ["GOOGLE_CLOUD_PROJECT"] = project_id_auth
os.environ["GOOGLE_CLOUD_LOCATION"] = "global"
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "True"

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
    email: str = ""
    username: str = ""
    password: str = ""
    
    document_statuses: Dict[str, str] = Field(default_factory=lambda: {
        "aadhaar_card": "unuploaded",
        "appointment_letter": "unuploaded",
        "teacher_eligibility_test": "unuploaded"
    })
    document_paths: Dict[str, str] = Field(default_factory=lambda: {
        "aadhaar_card": "",
        "appointment_letter": "",
        "teacher_eligibility_test": ""
    })
    pending_tally: int = 0
    current_stage: str = "document_collection"

    def update_document_upload_path(self, doc_type: str, filepath: str):
        if doc_type in self.document_statuses:
            self.document_statuses[doc_type] = "pending"
            self.document_paths[doc_type] = filepath
            print(f"[STATE TRANSITION] Document '{doc_type}' uploaded: {filepath}. Status set to 'pending'.")
            self.recalculate_pending_tally()

    def evaluate_document_approval(self, doc_type: str, approved: bool):
        status = "approved" if approved else "rejected"
        if doc_type in self.document_statuses:
            self.document_statuses[doc_type] = status
            if not approved:
                self.document_paths[doc_type] = ""
            print(f"[STATE TRANSITION] Document '{doc_type}' evaluation: {status}.")
            self.recalculate_pending_tally()
            self.check_all_approved_transition()

    def recalculate_pending_tally(self):
        self.pending_tally = sum(1 for status in self.document_statuses.values() if status == "pending")
        print(f"[STATE METRIC] Recalculated pending tally: {self.pending_tally}")

    def check_all_approved_transition(self):
        all_approved = all(status == "approved" for status in self.document_statuses.values())
        if all_approved:
            self.current_stage = "policy_review"
            print("[STATE TRANSITION] All documents approved! Advancing current_stage to 'policy_review'.")

def router_node(ctx: Context, node_input: Any) -> Event:
    local_store = LocalStateStore()
    stored_state = local_store.load_state()
    if stored_state:
        if "teachers" in stored_state and "teacher" in stored_state["teachers"]:
            teacher_data = stored_state["teachers"]["teacher"]
            for k in WorkflowState.model_fields.keys():
                if k in teacher_data:
                    ctx.state[k] = teacher_data[k]
        else:
            for k, v in stored_state.items():
                if k in WorkflowState.model_fields.keys():
                    ctx.state[k] = v

    text = str(node_input)
    if any(keyword in text.lower() for keyword in ["leave", "apply", "balance", "policy", "days"]):
        return Event(output=node_input, route="chatbot")
    return Event(output=node_input, route="onboarding")

def chatbot_node(ctx: Context, node_input: Any) -> Event:
    clean_input = DataMaskingMiddleware.redact_pii(str(node_input))
    response = ""
    state_updates = {}

    if "leave" in clean_input.lower() or "apply" in clean_input.lower():
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
        pinecone_service = PineconeRAGService()
        response = pinecone_service.query_rules(clean_input)

    local_store = LocalStateStore()
    current_state = local_store.load_state()
    current_state.update(state_updates)
    local_store.save_state(current_state)
    return Event(output=response, state=state_updates)

@node(rerun_on_resume=True)
@review_before_execute(api_action="Email HR & Candidate Interview Confirmation")
def initial_interview(ctx: Context, node_input: Any) -> Event:
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
    confirmation = ctx.state.get("confirmation_email_sent", False)
    if confirmation:
        state_updates = {"active_stage": "Procedures-Initiated"}
        local_store = LocalStateStore()
        current_state = local_store.load_state()
        current_state.update(state_updates)
        local_store.save_state(current_state)
        return Event(output="Confirmed: Initiating credentials, onboarding and scheduling tasks.", route="start_procedures", state=state_updates)
    return Event(output="Procedures halted: Confirmation email flag is False.", route="halted")

@node(rerun_on_resume=True)
@review_before_execute(api_action="Generate & dispatch secure portal credentials via SMTP")
async def credential_agent(ctx: Context, node_input: Any) -> Event:
    email = ctx.state.get("email") or "jane.doe@pes.edu"
    username = email
    
    existing_password = ctx.state.get("password")
    local_store = LocalStateStore()
    current_state = local_store.load_state()
    if not existing_password and current_state and "teachers" in current_state:
        for t_username, t_data in current_state["teachers"].items():
            if t_data.get("email") == email and t_data.get("password"):
                existing_password = t_data.get("password")
                break

    password = existing_password or secrets.token_urlsafe(10)
    name = ctx.state.get("candidate_name") or "Dr. Jane Doe"

    logging.info(f"Preparing to send credentials welcome email to {email}")

    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: 'Inter', sans-serif; background-color: #0d1117; color: #c9d1d9; margin: 0; padding: 20px; }}
        .card {{ background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 12px; padding: 30px; max-width: 600px; margin: auto; box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37); }}
        h2 {{ color: #58a6ff; margin-top: 0; }}
        p {{ line-height: 1.6; }}
        .credentials {{ background: rgba(255, 255, 255, 0.08); padding: 15px; border-radius: 8px; border-left: 4px solid #58a6ff; font-family: monospace; margin: 20px 0; }}
        .footer {{ font-size: 0.8em; color: #8b949e; text-align: center; margin-top: 30px; }}
    </style>
</head>
<body>
    <div class="card">
        <h2>Welcome to PES University {name}!</h2>
        <p>Dear Faculty Member,</p>
        <p>We are thrilled to welcome you to the PES University family. Your portal credentials have been successfully provisioned. Please log in using the details below:</p>
        <div class="credentials">
            <strong>Portal URL:</strong> http://localhost:8000<br>
            <strong>Username:</strong> {username}<br>
            <strong>Password:</strong> {password}
        </div>
        <p>After logging in, you will be guided through our onboarding workspace to upload your credentials and check university policy guidelines.</p>
        <p>Best Regards,<br>HR Department<br>PES University</p>
        <div class="footer">
            This is an automated onboarding email. Please do not reply directly.
        </div>
    </div>
</body>
</html>
"""
    def _send_email():
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = "Welcome to PES University - Portal Credentials"
            msg["From"] = SMTP_USERNAME
            msg["To"] = email
            msg.attach(MIMEText(html_content, "html"))
            print(f"[DEBUG SMTP] Destination email address: {email}")
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
                server.starttls()
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(SMTP_USERNAME, email, msg.as_string())
        except Exception as smtp_err:
            raise smtp_err

    try:
        await asyncio.to_thread(_send_email)
        logging.info(f"Successfully dispatched welcome email to {email}")
    except Exception as e:
        logging.error(f"Failed to dispatch welcome email to {email}: {e}")

    state_updates = {
        "credentials_sent": True,
        "active_stage": "Credentials-Sent",
        "username": username,
        "password": password
    }
    local_store = LocalStateStore()
    current_state = local_store.load_state()
    current_state.update(state_updates)
    
    for k, v in state_updates.items():
        ctx.state[k] = v

    if "teachers" in current_state:
        for t_username, t_data in current_state["teachers"].items():
            if t_data.get("email") == email:
                t_data.update(state_updates)
                break
    local_store.save_state(current_state)

    msg = f"Credentials Generated: Welcome email successfully dispatched via SMTP with TLS to {email}."
    return Event(output=msg, state=state_updates)

async def onboarding_guide(ctx: Context, node_input: Any):
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
    if isinstance(node_input, dict):
        res_val = node_input.get("uploaded_documents") or node_input.get("result") or list(node_input.values())[0]
    else:
        res_val = node_input

    clean_val = DataMaskingMiddleware.redact_pii(str(res_val))
    docs = [d.strip() for d in clean_val.split(",") if d.strip()]
    verified_files = [f for f in docs if f.endswith(('.pdf', '.docx'))]
    
    pinecone_service = PineconeRAGService()
    brief = pinecone_service.query_rules(",".join(verified_files))
    
    state_updates = {
        "policy_brief": brief,
        "active_stage": "Policy-Checked"
    }
    local_store = LocalStateStore()
    current_state = local_store.load_state()
    current_state.update(state_updates)
    local_store.save_state(current_state)

    msg = f"Policy RAG (Llama 3.1 Simulation) complete. Verified: {verified_files}.\n{brief}"
    return Event(output=msg, state=state_updates)

@node(rerun_on_resume=True)
@review_before_execute(api_action="Schedule calendar appointment and invite chairperson")
def scheduler_agent(ctx: Context, node_input: Any) -> Event:
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

@node(rerun_on_resume=True)
async def allotment_approval_gate(ctx: Context, node_input: Any):
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

@node(rerun_on_resume=True)
@review_before_execute(api_action="Notify IT and Administrative departments for campus provisioning")
def follow_up_provisioning(ctx: Context, node_input: Any) -> Event:
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

join_procedures = JoinNode(name="join_procedures")

edges_definition = [
    (START, router_node),
    (router_node, {"chatbot": chatbot_node, "onboarding": initial_interview}),
    (initial_interview, triggered_procedures),
    (triggered_procedures, {"start_procedures": (credential_agent, onboarding_guide, scheduler_agent)}),
    (onboarding_guide, policy_rag_agent),
    (scheduler_agent, {"email_chairperson": scheduler_agent, "final_presentation_secured": join_procedures}),
    ((credential_agent, policy_rag_agent), join_procedures),
    (join_procedures, allotment_approval_gate),
    (allotment_approval_gate, follow_up_provisioning)
]

state_manager_agent = Workflow(
    name="state_manager_agent",
    state_schema=WorkflowState,
    edges=edges_definition
)

adk_app = App(
    root_agent=state_manager_agent,
    name="app",
    resumability_config=ResumabilityConfig(enabled=True)
)



# =====================================================================
# 8. TYPING MODELS & SCHEMAS (formerly app/app_utils/typing.py)
# =====================================================================
class Feedback(BaseModel):
    """Represents feedback for a conversation."""
    score: int | float
    text: str | None = ""
    log_type: str = "feedback"
    service_name: str = "college-onboard-platform"
    user_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


# =====================================================================
# 9. API SERVER ROUTERS (formerly app/endpoints/routes.py)
# =====================================================================
router = APIRouter()

class ChatRequest(BaseModel):
    message: str

class ActionRequest(BaseModel):
    action: str
    payload: Optional[Any] = None

@router.get("/health")
def health_check() -> dict:
    return {"status": "healthy"}

@router.post("/webhook/upload")
def webhook_upload(payload: dict) -> dict:
    scrubbed = DataMaskingMiddleware.redact_pii(payload)
    return {
        "status": "processed",
        "details": "Payload successfully scrubbed & queued.",
        "payload": scrubbed
    }

@router.get("/api/state")
def get_state() -> dict:
    store = LocalStateStore()
    state = store.load_state()
    if not state or "teachers" not in state:
        state = initialize_default_state()
        store.save_state(state)
        return state

    modified = False
    for username, teacher in state.get("teachers", {}).items():
        if "document_statuses" not in teacher or not teacher["document_statuses"]:
            teacher["document_statuses"] = {
                "aadhaar_card": "unuploaded",
                "appointment_letter": "unuploaded",
                "teacher_eligibility_test": "unuploaded"
            }
            modified = True
            
        if "document_paths" not in teacher or not teacher["document_paths"]:
            teacher["document_paths"] = {
                "aadhaar_card": "",
                "appointment_letter": "",
                "teacher_eligibility_test": ""
            }
            modified = True

        uploaded = teacher.get("documents", [])
        verified = teacher.get("verified_documents", [])

        if len(uploaded) >= 1:
            doc_name = uploaded[0]
            teacher["document_paths"]["aadhaar_card"] = doc_name
            if doc_name in verified:
                teacher["document_statuses"]["aadhaar_card"] = "approved"
            elif teacher["document_statuses"]["aadhaar_card"] not in ["approved", "rejected"]:
                teacher["document_statuses"]["aadhaar_card"] = "pending"
            modified = True

        if len(uploaded) >= 2:
            doc_name = uploaded[1]
            teacher["document_paths"]["appointment_letter"] = doc_name
            if doc_name in verified:
                teacher["document_statuses"]["appointment_letter"] = "approved"
            elif teacher["document_statuses"]["appointment_letter"] not in ["approved", "rejected"]:
                teacher["document_statuses"]["appointment_letter"] = "pending"
            modified = True

        if len(uploaded) >= 3:
            doc_name = uploaded[2]
            teacher["document_paths"]["teacher_eligibility_test"] = doc_name
            if doc_name in verified:
                teacher["document_statuses"]["teacher_eligibility_test"] = "approved"
            elif teacher["document_statuses"]["teacher_eligibility_test"] not in ["approved", "rejected"]:
                teacher["document_statuses"]["teacher_eligibility_test"] = "pending"
            modified = True

    if modified:
        store.save_state(state)
    return state

def initialize_default_state() -> dict:
    return {
        "announcements": [
            {
                "id": 1,
                "title": "MedInnTech Minor Degree Program",
                "content": "Minor Degree in MedInnTech commences Monday, 6th July 2026. 22 Credits, 11 Courses, 5 Terms.",
                "date": "2026-07-01",
                "sender": "Chairperson"
            },
            {
                "id": 2,
                "title": "Ph.D Course Work Exam August 2026",
                "content": "Exam August 2026 Notification with Application form available in departments.",
                "date": "2026-07-01",
                "sender": "Admin"
            },
            {
                "id": 3,
                "title": "ESA June - July 2026 Backlog Room Allotment",
                "content": "Backlog Room Allotment Session-1 published for all undergraduate classes.",
                "date": "2026-07-02",
                "sender": "Admin"
            }
        ],
        "teachers": {
            "teacher": {
                "name": "Dr. Jane Doe",
                "email": "jane.doe@pes.edu",
                "department": "Computer Science & Engineering",
                "designation": "Professor",
                "username": "teacher",
                "password": "password",
                "seating_info": "Room 405, Desk C",
                "attendance": [
                    {"date": "2026-06-15", "status": "Absent", "reason": "Sick Leave"},
                    {"date": "2026-06-28", "status": "Absent", "reason": "Casual Leave"}
                ],
                "documents": ["PhD_Cert.pdf", "Joining_Letter.pdf"],
                "projects": [],
                "schedule": [
                    {"day": "Monday", "time": "09:00 AM - 10:30 AM", "class": "CSE-A", "subject": "Advanced Algorithms"},
                    {"day": "Wednesday", "time": "11:00 AM - 12:30 PM", "class": "CSE-B", "subject": "Machine Learning"},
                    {"day": "Friday", "time": "02:00 PM - 03:30 PM", "class": "CSE-A", "subject": "Advanced Algorithms"}
                ],
                "policy_brief": "[Pinecone Search @ production] RETRIEVED RULES CONTEXT:\n- Joining guidelines: Submit original verification documents within 30 days.\n- Campus ethics: Absolute professionalism in research and teaching duties.",
                "leave_balance": 28
            }
        }
    }

@router.post("/api/state/reset")
def reset_state() -> dict:
    store = LocalStateStore()
    state = initialize_default_state()
    store.save_state(state)
    write_log("SYSTEM", "State reset to default mock values.")
    return state

def refine_query_with_gemini(user_input: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return user_input

    refiner_prompt = (
        "You are an expert Query Refiner for the PESU HR & Policy RAG system.\n"
        "Your goal is to transform vague or conversational user questions into precise search queries that will maximize the retrieval of accurate policy information from our Pinecone database.\n\n"
        "### Guidelines for Refinement:\n"
        "1. Identify the core intent of the user's question.\n"
        "2. Do not answer the question; only rewrite it to be optimal for vector search.\n"
        "3. If the user uses colloquial language, translate it into standard HR/Institutional terminology.\n"
        "4. If the query is already precise, keep it as is.\n\n"
        f"User Input: \"{user_input}\"\n"
        "Refined Query:"
    )

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        data = {"contents": [{"parts": [{"text": refiner_prompt}]}]}
        response = requests.post(url, headers=headers, json=data, timeout=15.0)
        if response.status_code == 200:
            refined = response.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            if refined.startswith('"') and refined.endswith('"'):
                refined = refined[1:-1].strip()
            write_log("QUERY_REFINER", f"Refined '{user_input}' -> '{refined}'")
            return refined
    except Exception as e:
        write_log("QUERY_REFINER_ERROR", f"Failed to refine query: {str(e)}")
    return user_input

@router.post("/api/chat")
def chatbot_endpoint(req: ChatRequest) -> dict:
    clean_input = DataMaskingMiddleware.redact_pii(req.message)
    write_log("CHATBOT_AGENT", f"Received message: '{clean_input}'")
    
    if req.message == "load_basic_policies_rag":
        pinecone_service = PineconeRAGService()
        rules_context = pinecone_service.query_rules("core university guidelines, employee ethics, campus policies, faculty code of conduct")
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        prompt = (
            f"You are a helpful PESU AI. Please synthesize the following retrieved university policies, faculty code of conduct, and employee guidelines into a welcoming, easy-to-digest executive brief for a newly onboarded teacher. Start with a warm welcome statement, highlight the core values, working expectations, and code of conduct. Keep it structured with bullet points.\n\n"
            f"Retrieved Policies:\n{rules_context}\n\n"
            f"Executive Brief:"
        )
    else:
        refined_query = refine_query_with_gemini(clean_input)
        pinecone_service = PineconeRAGService()
        rules_context = pinecone_service.query_rules(refined_query)
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        prompt = (
            f"You are a helpful PESU AI. Use the following Pinecone RAG context to answer the user's query.\n"
            f"If you answer using the retrieved context guidelines, always append '[Source: Pinecone Database]' to make it clear that the response refers to retrieved records.\n"
            f"If the context does not contain enough info to answer the query, reply to the best of your knowledge, specify that it is general info, and do not append the citation.\n\n"
            f"Context:\n{rules_context}\n\n"
            f"User Query: {clean_input}\n\n"
            f"Response:"
        )
    
    answer = None
    if api_key:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            headers = {"Content-Type": "application/json"}
            data = {"contents": [{"parts": [{"text": prompt}]}]}
            retries = 3
            backoff = 0.5
            for attempt in range(retries):
                response = requests.post(url, headers=headers, json=data, timeout=15.0)
                if response.status_code == 200:
                    answer = response.json()["candidates"][0]["content"]["parts"][0]["text"]
                    break
                elif response.status_code == 503 and attempt < retries - 1:
                    time.sleep(backoff)
                    backoff *= 2
                else:
                    write_log("CHATBOT_ERROR", f"Gemini API error {response.status_code}: {response.text}")
                    break
        except Exception as e:
            write_log("CHATBOT_ERROR", f"Failed to contact Gemini API: {str(e)}")

    if not answer:
        answer = f"[RAG Rules Context] Retrieved Rules:\n{rules_context}\n\n(Please check that GEMINI_API_KEY in .env is valid)"
    return {"response": answer}

def send_welcome_email_task(email: str, username: str, name: str, password: str):
    logging.info(f"Preparing to send credentials welcome email to {email}")
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: 'Inter', sans-serif; background-color: #0d1117; color: #c9d1d9; margin: 0; padding: 20px; }}
        .card {{ background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 12px; padding: 30px; max-width: 600px; margin: auto; box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37); }}
        h2 {{ color: #58a6ff; margin-top: 0; }}
        p {{ line-height: 1.6; }}
        .credentials {{ background: rgba(255, 255, 255, 0.08); padding: 15px; border-radius: 8px; border-left: 4px solid #58a6ff; font-family: monospace; margin: 20px 0; }}
        .footer {{ font-size: 0.8em; color: #8b949e; text-align: center; margin-top: 30px; }}
    </style>
</head>
<body>
    <div class="card">
        <h2>Welcome to PES University {name}!</h2>
        <p>Dear Faculty Member,</p>
        <p>We are thrilled to welcome you to the PES University family. Your portal credentials have been successfully provisioned. Please log in using the details below:</p>
        <div class="credentials">
            <strong>Portal URL:</strong> http://localhost:8000<br>
            <strong>Username:</strong> {username}<br>
            <strong>Password:</strong> {password}
        </div>
        <p>After logging in, you will be guided through our onboarding workspace to upload your credentials and check university policy guidelines.</p>
        <p>Best Regards,<br>HR Department<br>PES University</p>
        <div class="footer">
            This is an automated onboarding email. Please do not reply directly.
        </div>
    </div>
</body>
</html>
"""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Welcome to PES University - Portal Credentials"
        msg["From"] = SMTP_USERNAME
        msg["To"] = email
        msg.attach(MIMEText(html_content, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_USERNAME, email, msg.as_string())
        logging.info(f"Successfully dispatched welcome email to {email}")
    except Exception as e:
        logging.error(f"Failed to dispatch welcome email to {email}: {e}")

def send_verification_email_task(email: str, name: str):
    logging.info(f"Preparing to send verification confirmation email to {email}")
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: 'Inter', sans-serif; background-color: #0d1117; color: #c9d1d9; margin: 0; padding: 20px; }}
        .card {{ background: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.1); border-radius: 12px; padding: 30px; max-width: 600px; margin: auto; box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37); }}
        h2 {{ color: #58a6ff; margin-top: 0; }}
        p {{ line-height: 1.6; }}
        .footer {{ font-size: 0.8em; color: #8b949e; text-align: center; margin-top: 30px; }}
    </style>
</head>
<body>
    <div class="card">
        <h2>Documents Verified - PES University</h2>
        <p>Dear Faculty Member,</p>
        <p>We are pleased to inform you that all your submitted verification documents (Aadhaar Card, Appointment Letter, and Teacher Eligibility Test) have been successfully verified by our HR department.</p>
        <p>You need to log in to the <a href="http://localhost:8000" style="color: #58a6ff;">PESU Academic portal</a> and check the <strong>PESU AI</strong> chatbot for a detailed brief on college policies.</p>
        <p>Best Regards,<br>HR Department<br>PES University</p>
        <div class="footer">
            This is an automated onboarding email. Please do not reply directly.
        </div>
    </div>
</body>
</html>
"""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = "Documents Verified - PES University Onboarding"
        msg["From"] = SMTP_USERNAME
        msg["To"] = email
        msg.attach(MIMEText(html_content, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_USERNAME, email, msg.as_string())
        logging.info(f"Successfully dispatched verification email to {email}")
    except Exception as e:
        logging.error(f"Failed to dispatch verification email to {email}: {e}")

@router.post("/api/action")
def trigger_action(req: ActionRequest, background_tasks: BackgroundTasks) -> dict:
    store = LocalStateStore()
    state = store.load_state()
    if not state or "teachers" not in state:
        state = initialize_default_state()

    action = req.action
    payload = req.payload

    if action == "add_teacher":
        email = payload.get("email")
        if not email:
            raise HTTPException(status_code=400, detail="Email is required.")
        username = email
        if username in state["teachers"]:
            raise HTTPException(status_code=400, detail="Teacher with this email already exists.")
        
        name = payload.get("name")
        password = secrets.token_urlsafe(10)

        state["teachers"][username] = {
            "name": name,
            "email": email,
            "department": payload.get("department", "CSE"),
            "designation": payload.get("designation", "Assistant Professor"),
            "username": username,
            "password": password,
            "seating_info": "Not Allotted",
            "attendance": [
                {"date": "2026-06-10", "status": "Absent", "reason": "Personal Leave"},
                {"date": "2026-06-20", "status": "Absent", "reason": "Medical Leave"}
            ],
            "documents": [],
            "projects": [],
            "schedule": [
                {"day": "Tuesday", "time": "10:00 AM - 11:30 AM", "class": "CSE-C", "subject": "Database Systems"},
                {"day": "Thursday", "time": "02:00 PM - 03:30 PM", "class": "CSE-C", "subject": "Database Systems"}
            ],
            "policy_brief": "Pending document upload and policy checker run.",
            "leave_balance": 30
        }
        write_log("HR_AGENT", f"New teacher profile created: {username} ({name})")
        background_tasks.add_task(send_welcome_email_task, email=email, username=username, name=name, password=password)

    elif action == "update_teacher":
        username = payload.get("username")
        if username not in state["teachers"]:
            raise HTTPException(status_code=404, detail="Teacher not found.")
        
        state["teachers"][username].update({
            "name": payload.get("name"),
            "email": payload.get("email"),
            "department": payload.get("department"),
            "designation": payload.get("designation")
        })
        write_log("HR_AGENT", f"Updated profile details for teacher: {username}")

    elif action == "delete_teacher":
        username = payload.get("username")
        if username not in state["teachers"]:
            raise HTTPException(status_code=404, detail="Teacher not found.")
        del state["teachers"][username]
        write_log("HR_AGENT", f"Deleted teacher profile: {username}")

    elif action == "allot_seat":
        username = payload.get("username")
        seating = payload.get("seating_info")
        if username not in state["teachers"]:
            raise HTTPException(status_code=404, detail="Teacher not found.")
        
        state["teachers"][username]["seating_info"] = seating
        write_log("CHAIRPERSON_AGENT", f"Allotted seating '{seating}' for teacher {username}.")

    elif action == "add_announcement":
        announcement_id = len(state.get("announcements", [])) + 1
        new_ann = {
            "id": announcement_id,
            "title": payload.get("title"),
            "content": payload.get("content"),
            "date": datetime.datetime.now().strftime("%Y-%m-%d"),
            "sender": payload.get("sender", "Admin")
        }
        state["announcements"].append(new_ann)
        write_log("ADMIN_AGENT", f"New announcement published: '{payload.get('title')}'")

    elif action == "upload_document":
        username = payload.get("username")
        doc_name = payload.get("document_name")
        doc_type = payload.get("doc_type", "aadhaar_card")
        if username not in state["teachers"]:
            raise HTTPException(status_code=404, detail="Teacher not found.")
        
        teacher_data = state["teachers"][username]
        matching_fields = {k: v for k, v in teacher_data.items() if k in WorkflowState.model_fields}
        ws = WorkflowState(**matching_fields)
        
        if doc_name not in ws.documents:
            ws.documents.append(doc_name)
            
        ws.update_document_upload_path(doc_type, doc_name)
        state["teachers"][username].update(ws.model_dump())
        
        pinecone_service = PineconeRAGService()
        brief = pinecone_service.query_rules(doc_name)
        state["teachers"][username]["policy_brief"] = brief
        write_log("CANDIDATE_PORTAL", f"Uploaded document: {doc_name} for teacher {username}")

    elif action == "verify_document":
        username = payload.get("username")
        doc_name = payload.get("document_name")
        doc_type = payload.get("doc_type", "aadhaar_card")
        approved = payload.get("approved", True)
        if username not in state["teachers"]:
            raise HTTPException(status_code=404, detail="Teacher not found.")
        
        teacher_data = state["teachers"][username]
        matching_fields = {k: v for k, v in teacher_data.items() if k in WorkflowState.model_fields}
        ws = WorkflowState(**matching_fields)
        
        ws.evaluate_document_approval(doc_type, approved)
        
        if "verified_documents" not in state["teachers"][username]:
            state["teachers"][username]["verified_documents"] = []
            
        if approved:
            if doc_name not in state["teachers"][username]["verified_documents"]:
                state["teachers"][username]["verified_documents"].append(doc_name)
        else:
            if doc_name in state["teachers"][username]["verified_documents"]:
                state["teachers"][username]["verified_documents"].remove(doc_name)
            if doc_name in ws.documents:
                ws.documents.remove(doc_name)
            ws.document_statuses[doc_type] = "rejected"
            
        state["teachers"][username].update(ws.model_dump())
        write_log("HR_PORTAL", f"Evaluated document '{doc_name}' for teacher {username}: approved={approved}")

        if ws.current_stage == "policy_review":
            email = state["teachers"][username].get("email")
            name = state["teachers"][username].get("name", "Faculty Member")
            if email:
                background_tasks.add_task(send_verification_email_task, email=email, name=name)
    else:
        raise HTTPException(status_code=400, detail="Invalid action")

    store.save_state(state)
    return {"status": "success", "state": state}

@router.get("/api/logs")
def get_logs() -> List[dict]:
    log_file = "agent_activity.log"
    if not os.path.exists(log_file):
        return []
    try:
        with open(log_file, "r") as f:
            lines = f.readlines()
        return [json.loads(line) for line in lines[-50:]]
    except Exception:
        return []

def write_log(agent: str, message: str):
    log_file = "agent_activity.log"
    log_entry = {
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "agent": agent,
        "message": message
    }
    try:
        with open(log_file, "a") as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception:
        pass


# =====================================================================
# 10. FASTAPI APP INITIALIZATION & SETUP
# =====================================================================
setup_telemetry()
otel_to_cloud = True
project_id = "mock-project-id"

# Bypass GCP metadata checking when running Streamlit to avoid blocking/timeouts
if st.runtime.exists():
    otel_to_cloud = False
else:
    try:
        _, project_id = google.auth.default()
    except Exception:
        otel_to_cloud = False

try:
    if not otel_to_cloud:
        raise ValueError("Skipping cloud logging for local/Streamlit run")
    logging_client = google_cloud_logging.Client()
    logger = logging_client.logger(__name__)
except Exception:
    import logging
    logging.basicConfig(level=logging.INFO)
    class LocalLogger:
        def log_struct(self, data, severity="INFO"):
            logging.info(f"[{severity}] {data}")
        def info(self, msg):
            logging.info(msg)
        def error(self, msg):
            logging.error(msg)
        def warning(self, msg):
            logging.warning(msg)
    logger = LocalLogger()


allow_origins = (
    os.getenv("ALLOW_ORIGINS", "").split(",") if os.getenv("ALLOW_ORIGINS") else None
)

logs_bucket_name = os.environ.get("LOGS_BUCKET_NAME")
AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
session_service_uri = None
artifact_service_uri = f"gs://{logs_bucket_name}" if logs_bucket_name else None

app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=False,
    artifact_service_uri=artifact_service_uri,
    allow_origins=allow_origins,
    session_service_uri=session_service_uri,
    otel_to_cloud=otel_to_cloud,
)
app.title = "college-onboard-platform"
app.description = "API for interacting with the Agent college-onboard-platform"

app.include_router(router)
app.router.routes = [r for r in app.router.routes if not hasattr(r, "path") or r.path != "/"]

static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
def read_index():
    return FileResponse(os.path.join(static_dir, "index.html"))

async def ambient_background_worker():
    store = LocalStateStore()
    while True:
        try:
            state = store.load_state()
        except Exception:
            pass
        await asyncio.sleep(5)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(ambient_background_worker())

@app.post("/feedback")
def collect_feedback(feedback: Feedback) -> dict[str, str]:
    logger.log_struct(feedback.model_dump(), severity="INFO")
    return {"status": "success"}


# =====================================================================
# 11. STREAMLIT UI RENDERING BLOCK
# =====================================================================
if st.runtime.exists():
    st.set_page_config(
        page_title="PES University Academic Portal",
        page_icon="🎓",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.markdown("""
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Outfit:wght@400;600;800&display=swap');
        
        html, body, [class*="css"], .stApp {
            font-family: 'Inter', sans-serif;
        }
        
        h1, h2, h3, h4, h5, h6 {
            font-family: 'Outfit', sans-serif;
            color: #f8fafc;
        }
        
        .stApp {
            background: radial-gradient(circle at 10% 20%, rgba(15, 23, 42, 1) 0%, rgba(8, 10, 15, 1) 90%);
        }
        
        .glass-card {
            background: rgba(30, 41, 59, 0.45);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 16px;
            padding: 24px;
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
            backdrop-filter: blur(8px);
            -webkit-backdrop-filter: blur(8px);
            margin-bottom: 20px;
        }
        
        .badge {
            display: inline-block;
            padding: 6px 12px;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-top: 4px;
        }
        
        .badge-primary {
            background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%);
            color: white;
        }
        .badge-success {
            background: linear-gradient(135deg, #10b981 0%, #047857 100%);
            color: white;
        }
        .badge-warning {
            background: linear-gradient(135deg, #f59e0b 0%, #b45309 100%);
            color: white;
        }
        .badge-danger {
            background: linear-gradient(135deg, #ef4444 0%, #b91c1c 100%);
            color: white;
        }
        
        .logo-container {
            display: flex;
            align-items: center;
            gap: 16px;
            margin-bottom: 24px;
        }
        .logo-icon {
            font-size: 3rem;
        }
        .logo-text h1 {
            margin: 0;
            font-size: 1.8rem;
            font-weight: 800;
            background: linear-gradient(to right, #38bdf8, #818cf8);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        div.stButton > button {
            background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%);
            color: white;
            border: none;
            padding: 10px 24px;
            border-radius: 8px;
            font-weight: 600;
            box-shadow: 0 4px 12px rgba(29, 78, 216, 0.3);
            transition: all 0.3s ease;
        }
        div.stButton > button:hover {
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(29, 78, 216, 0.4);
        }
        
        .css-1d391tw {
            background-color: #0f172a !important;
        }
    </style>
    """, unsafe_allow_html=True)

    store = LocalStateStore()
    state = store.load_state()
    if not state or "teachers" not in state:
        state = initialize_default_state()
        store.save_state(state)

    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
        st.session_state.username = ""
        st.session_state.role = ""
        st.session_state.teacher_key = ""

    def logout():
        st.session_state.logged_in = False
        st.session_state.username = ""
        st.session_state.role = ""
        st.session_state.teacher_key = ""
        st.rerun()

    if not st.session_state.logged_in:
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.markdown('<div style="height: 80px;"></div>', unsafe_allow_html=True)
            st.markdown("""
            <div class="glass-card" style="text-align: center;">
                <div style="font-size: 4rem;">🎓</div>
                <h1 style="margin: 0; background: linear-gradient(to right, #38bdf8, #818cf8); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-weight: 800;">PES UNIVERSITY</h1>
                <p style="color: #94a3b8; font-size: 1.1rem; margin-top: 5px; margin-bottom: 25px;">Academic & Onboarding Portal</p>
            </div>
            """, unsafe_allow_html=True)
            
            with st.form("login_form"):
                username_input = st.text_input("Username", placeholder="Enter username (e.g. teacher, hr, admin)")
                password_input = st.text_input("Password", placeholder="Enter password", type="password")
                submit_button = st.form_submit_button("Sign In Securely", use_container_width=True)
                
                if submit_button:
                    if username_input == "admin" and password_input == "password":
                        st.session_state.logged_in = True
                        st.session_state.username = "Administrator"
                        st.session_state.role = "Admin"
                        st.success("Successfully logged in as Admin!")
                        st.rerun()
                    elif username_input == "hr" and password_input == "password":
                        st.session_state.logged_in = True
                        st.session_state.username = "HR Manager"
                        st.session_state.role = "HR"
                        st.success("Successfully logged in as HR!")
                        st.rerun()
                    elif username_input in state.get("teachers", {}):
                        teacher_data = state["teachers"][username_input]
                        if password_input == teacher_data.get("password"):
                            st.session_state.logged_in = True
                            st.session_state.username = teacher_data.get("name", "Faculty Member")
                            st.session_state.role = "Teacher"
                            st.session_state.teacher_key = username_input
                            st.success(f"Successfully logged in as {st.session_state.username}!")
                            st.rerun()
                        else:
                            st.error("Incorrect password for this user.")
                    else:
                        st.error("Invalid credentials. Please try again.")


    else:
        with st.sidebar:
            st.markdown(f"""
            <div style="text-align: center; margin-bottom: 20px;">
                <div style="font-size: 3rem;">🎓</div>
                <h3 style="margin: 0; background: linear-gradient(to right, #38bdf8, #818cf8); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-weight: 700;">PES UNIVERSITY</h3>
                <span class="badge badge-primary">Role: {st.session_state.role}</span>
            </div>
            """, unsafe_allow_html=True)
            
            st.markdown(f"""
            <div class="glass-card" style="padding: 15px; text-align: center;">
                <div style="font-size: 2.5rem; margin-bottom: 5px;">👤</div>
                <h4 style="margin: 0; font-size: 1.1rem;">{st.session_state.username}</h4>
                <p style="color: #94a3b8; font-size: 0.85rem; margin-top: 2px; margin-bottom: 0;">{st.session_state.role if st.session_state.role != "Teacher" else state["teachers"][st.session_state.teacher_key].get("email")}</p>
            </div>
            """, unsafe_allow_html=True)
            
            if st.session_state.role == "Teacher":
                menu_options = [
                    "My Profile",
                    "Academic Calendar",
                    "PESU AI Chatbot",
                    "My Attendance",
                    "Seating Info",
                    "Submit Documents",
                    "Projects & Publications"
                ]
            elif st.session_state.role == "HR":
                menu_options = [
                    "Manage Teachers",
                    "Verification Hub",
                    "Add New Teacher"
                ]
            else:
                menu_options = [
                    "Allot Seating",
                    "Broadcast Announcement",
                    "All Teachers Overview"
                ]
                
            choice = st.radio("Navigation", menu_options, label_visibility="collapsed")
            st.markdown('<div style="height: 40px;"></div>', unsafe_allow_html=True)
            if st.button("Sign Out", use_container_width=True):
                logout()

        if st.session_state.role == "Teacher":
            teacher_email = st.session_state.teacher_key
            teacher_info = state["teachers"][teacher_email]
            
            if "document_statuses" not in teacher_info or not teacher_info["document_statuses"]:
                teacher_info["document_statuses"] = {
                    "aadhaar_card": "unuploaded",
                    "appointment_letter": "unuploaded",
                    "teacher_eligibility_test": "unuploaded"
                }
                store.save_state(state)

            if "document_paths" not in teacher_info or not teacher_info["document_paths"]:
                teacher_info["document_paths"] = {
                    "aadhaar_card": "",
                    "appointment_letter": "",
                    "teacher_eligibility_test": ""
                }
                store.save_state(state)

            st.markdown(f"## Welcome back, {st.session_state.username}!")
            
            if choice == "My Profile":
                st.markdown("### My Profile")
                st.markdown("Personal academic record details and designation info.")
                st.markdown(f"""
                <div class="glass-card">
                    <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 15px;">
                        <div><strong>Full Name:</strong> {teacher_info.get("name")}</div>
                        <div><strong>Email Address:</strong> {teacher_info.get("email")}</div>
                        <div><strong>Department:</strong> {teacher_info.get("department")}</div>
                        <div><strong>Designation:</strong> {teacher_info.get("designation")}</div>
                        <div><strong>Leave Balance:</strong> {teacher_info.get("leave_balance", 30)} days</div>
                        <div><strong>Onboarding Stage:</strong> <span class="badge badge-primary">{teacher_info.get("current_stage", "document_collection")}</span></div>
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
            elif choice == "Academic Calendar":
                st.markdown("### Academic Schedule")
                st.markdown("Your official schedule for lectures, labs, and discussions.")
                schedule = teacher_info.get("schedule", [])
                if schedule:
                    st.table(schedule)
                else:
                    st.info("No classes scheduled yet.")
                    
            elif choice == "PESU AI Chatbot":
                st.markdown("### PESU AI Chatbot")
                st.markdown("Ask anything about PES University guidelines, leaves, and campus rules.")
                
                if "chat_history" not in st.session_state:
                    st.session_state.chat_history = [
                        {"role": "assistant", "content": "Hello, how can I help you today? Ask me about PES policies or leave rules."}
                    ]
                
                for msg in st.session_state.chat_history:
                    avatar = "🤖" if msg["role"] == "assistant" else "👤"
                    with st.chat_message(msg["role"], avatar=avatar):
                        st.write(msg["content"])
                
                user_msg = st.chat_input("Ask a policy question...")
                if user_msg:
                    st.session_state.chat_history.append({"role": "user", "content": user_msg})
                    with st.chat_message("user", avatar="👤"):
                        st.write(user_msg)
                    
                    clean_input = DataMaskingMiddleware.redact_pii(user_msg)
                    write_log("CHATBOT_AGENT", f"Received message: '{clean_input}'")
                    
                    if user_msg == "load_basic_policies_rag":
                        rules_context = PineconeRAGService().query_rules("core university guidelines, employee ethics, campus policies, faculty code of conduct")
                        api_key = os.getenv("GEMINI_API_KEY", "").strip()
                        prompt = (
                            f"You are a helpful PESU AI. Please synthesize the following retrieved university policies, faculty code of conduct, and employee guidelines into a welcoming, easy-to-digest brief for a newly onboarded teacher. Start with a warm welcome, highlight core values, and expectations. Keep it structured with bullet points.\n\n"
                            f"Retrieved Policies:\n{rules_context}\n\nBrief:"
                        )
                    else:
                        refined_query = refine_query_with_gemini(clean_input)
                        rules_context = PineconeRAGService().query_rules(refined_query)
                        api_key = os.getenv("GEMINI_API_KEY", "").strip()
                        prompt = (
                            f"You are a helpful PESU AI. Use the following Pinecone RAG context to answer the user's query.\n"
                            f"If you answer using the retrieved context guidelines, always append '[Source: Pinecone Database]'.\n"
                            f"If the context does not contain enough info, reply to the best of your knowledge, specify that it is general info, and do not append the citation.\n\n"
                            f"Context:\n{rules_context}\n\nUser Query: {clean_input}\n\nResponse:"
                        )
                    
                    answer = None
                    if api_key:
                        try:
                            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
                            headers = {"Content-Type": "application/json"}
                            data = {"contents": [{"parts": [{"text": prompt}]}]}
                            response = requests.post(url, headers=headers, json=data, timeout=15.0)
                            if response.status_code == 200:
                                answer = response.json()["candidates"][0]["content"]["parts"][0]["text"]
                        except Exception as e:
                            write_log("CHATBOT_ERROR", f"Failed to contact Gemini: {str(e)}")
                    
                    if not answer:
                        answer = f"[RAG Rules Context] Simulated retrieved records:\n{rules_context}\n\n(Please check that GEMINI_API_KEY is valid)"
                    
                    st.session_state.chat_history.append({"role": "assistant", "content": answer})
                    with st.chat_message("assistant", avatar="🤖"):
                        st.write(answer)
                        
            elif choice == "My Attendance":
                st.markdown("### Attendance Summary")
                st.markdown("Summary of official present and absent logs.")
                absences = teacher_info.get("attendance", [])
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("Present Days", "24")
                with col2:
                    st.metric("Absent Days", len(absences))
                    
                st.write("#### Absence Log Records")
                if absences:
                    st.table(absences)
                else:
                    st.info("No absent records.")
                    
            elif choice == "Seating Info":
                st.markdown("### Seating Allotment")
                st.markdown("Assigned seating space inside PESU campus.")
                seating = teacher_info.get("seating_info", "Not Allotted")
                st.info(f"**Current Seating:** {seating}")
                
            elif choice == "Submit Documents":
                st.markdown("### Submit Verification Documents")
                st.markdown("Upload scanned PDF copies of your identification files to trigger the HR review process.")
                
                doc_statuses = teacher_info["document_statuses"]
                doc_paths = teacher_info["document_paths"]
                
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("#### Document Checklist Status")
                    for key, val in doc_statuses.items():
                        badge_style = "badge-success" if val == "approved" else "badge-warning" if val == "pending" else "badge-danger"
                        display_name = key.replace("_", " ").title()
                        st.markdown(f"- **{display_name}:** <span class=\"badge {badge_style}\">{val}</span>", unsafe_allow_html=True)
                
                with col2:
                    st.markdown("#### Upload Documents")
                    doc_type_choice = st.selectbox("Select document type to upload", ["aadhaar_card", "appointment_letter", "teacher_eligibility_test"])
                    uploaded_file = st.file_uploader(f"Choose file for {doc_type_choice.replace('_', ' ').title()}", type=["pdf"])
                    
                    if uploaded_file is not None:
                        if st.button("Submit Document"):
                            doc_name = uploaded_file.name
                            
                            # Save actual file to disk for HR viewing
                            os.makedirs("uploaded_docs", exist_ok=True)
                            with open(os.path.join("uploaded_docs", doc_name), "wb") as f:
                                f.write(uploaded_file.getbuffer())
                                
                            matching_fields = {k: v for k, v in teacher_info.items() if k in WorkflowState.model_fields}
                            ws = WorkflowState(**matching_fields)
                            
                            if doc_name not in ws.documents:
                                ws.documents.append(doc_name)
                            
                            ws.update_document_upload_path(doc_type_choice, doc_name)
                            
                            pinecone_service = PineconeRAGService()
                            brief = pinecone_service.query_rules(doc_name)
                            ws.policy_brief = brief
                            
                            teacher_info.update(ws.model_dump())
                            state["teachers"][teacher_email] = teacher_info
                            store.save_state(state)
                            
                            write_log("CANDIDATE_PORTAL", f"Uploaded document: {doc_name} for teacher {teacher_email}")
                            st.success(f"Successfully uploaded {doc_name}! Status updated to pending.")
                            st.rerun()
                            
            elif choice == "Projects & Publications":
                st.markdown("### Projects & Publications")
                st.markdown("Keep track of your current funded research projects and journal articles.")
                projects = teacher_info.get("projects", [])
                
                with st.form("new_project_form"):
                    proj_title = st.text_input("Project/Publication Title")
                    proj_desc = st.text_area("Description")
                    submitted = st.form_submit_button("Add Record")
                    if submitted and proj_title:
                        projects.append({"title": proj_title, "description": proj_desc, "date": datetime.datetime.now().strftime("%Y-%m-%d")})
                        teacher_info["projects"] = projects
                        state["teachers"][teacher_email] = teacher_info
                        store.save_state(state)
                        st.success("Record added successfully!")
                        st.rerun()
                
                if projects:
                    for idx, proj in enumerate(projects):
                        st.markdown(f"""
                        <div class="glass-card">
                            <h5>{proj.get('title')} ({proj.get('date')})</h5>
                            <p style="margin:0; color:#cbd5e1;">{proj.get('description')}</p>
                        </div>
                        """, unsafe_allow_html=True)
                else:
                    st.info("No projects or publications added yet.")

        elif st.session_state.role == "HR":
            st.markdown("## HR Management Dashboard")
            
            if choice == "Manage Teachers":
                st.markdown("### Manage Teachers")
                st.markdown("Modify, update or remove existing faculty profiles.")
                
                teachers_dict = state.get("teachers", {})
                if teachers_dict:
                    for key, teacher in list(teachers_dict.items()):
                        with st.expander(f"{teacher.get('name')} ({teacher.get('email')})"):
                            with st.form(f"edit_form_{key}"):
                                name = st.text_input("Name", value=teacher.get("name"))
                                dept = st.text_input("Department", value=teacher.get("department"))
                                desig = st.text_input("Designation", value=teacher.get("designation"))
                                
                                col1, col2 = st.columns(2)
                                with col1:
                                    if st.form_submit_button("Update Details"):
                                        teacher["name"] = name
                                        teacher["department"] = dept
                                        teacher["designation"] = desig
                                        state["teachers"][key] = teacher
                                        store.save_state(state)
                                        write_log("HR_AGENT", f"Updated details for teacher: {key}")
                                        st.success("Details updated successfully!")
                                        st.rerun()
                                with col2:
                                    if st.form_submit_button("Delete Profile"):
                                        del state["teachers"][key]
                                        store.save_state(state)
                                        write_log("HR_AGENT", f"Deleted teacher profile: {key}")
                                        st.success("Profile deleted successfully!")
                                        st.rerun()
                else:
                    st.info("No teachers registered.")
                    
            elif choice == "Verification Hub":
                st.markdown("### Verification Hub")
                st.markdown("Review and evaluate uploaded verification files.")
                
                if "preview_file" in st.session_state and st.session_state.preview_file:
                    filename = st.session_state.preview_file
                    filepath = os.path.join("uploaded_docs", filename)
                    
                    if os.path.exists(filepath):
                        import base64
                        try:
                            with open(filepath, "rb") as f:
                                file_bytes = f.read()
                                base64_pdf = base64.b64encode(file_bytes).decode('utf-8')
                            
                            st.markdown(f"#### 📄 Previewing: `{filename}`")
                            
                            # Provide a download button renamed to Preview (the iframe preview has been removed)
                            st.download_button(
                                label="Preview",
                                data=file_bytes,
                                file_name=filename,
                                mime="application/pdf"
                            )
                        except Exception as e:
                            st.error(f"Error loading PDF preview: {str(e)}")
                    else:
                        st.markdown(f"""
                        <div class="glass-card" style="border-left: 5px solid #38bdf8; padding: 20px; margin-bottom: 20px;">
                            <h4 style="margin-top:0; color:#38bdf8;">📄 Document Preview: {filename}</h4>
                            <p style="font-size:0.95rem; color:#cbd5e1; margin-bottom:15px;">
                                This is a simulated verification view for administrative preview of the submitted PDF asset.
                            </p>
                        </div>
                        """, unsafe_allow_html=True)
                        
                    if st.button("Close Preview"):
                        st.session_state.preview_file = None
                        st.rerun()


                teachers_dict = state.get("teachers", {})
                pending_verifications = False
                
                for key, teacher in teachers_dict.items():
                    doc_statuses = teacher.get("document_statuses", {})
                    doc_paths = teacher.get("document_paths", {})
                    has_pending = any(status == "pending" for status in doc_statuses.values())
                    if has_pending:
                        pending_verifications = True
                        st.markdown(f"#### Teacher: {teacher.get('name')} ({teacher.get('email')})")
                        
                        for doc_type, status in doc_statuses.items():
                            if status == "pending":
                                filename = doc_paths.get(doc_type, "N/A")
                                st.write(f"- **{doc_type.replace('_', ' ').title()}:** File `{filename}` is pending approval.")
                                col1, col2, col3 = st.columns(3)
                                with col1:
                                    if st.button("Preview", key=f"prev_{key}_{doc_type}"):
                                        st.session_state.preview_file = filename
                                        st.rerun()
                                with col2:
                                    if st.button("Approve", key=f"app_{key}_{doc_type}"):
                                        matching_fields = {k: v for k, v in teacher.items() if k in WorkflowState.model_fields}
                                        ws = WorkflowState(**matching_fields)
                                        ws.evaluate_document_approval(doc_type, True)
                                        
                                        if "verified_documents" not in teacher:
                                            teacher["verified_documents"] = []
                                        if filename not in teacher["verified_documents"]:
                                            teacher["verified_documents"].append(filename)
                                            
                                        teacher.update(ws.model_dump())
                                        state["teachers"][key] = teacher
                                        store.save_state(state)
                                        write_log("HR_PORTAL", f"Evaluated document '{filename}' for teacher {key}: approved=True")
                                        
                                        if ws.current_stage == "policy_review":
                                            send_verification_email_task(email=teacher.get("email"), name=teacher.get("name"))
                                            
                                        st.success(f"Approved {doc_type}!")
                                        st.rerun()
                                with col3:
                                    if st.button("Reject", key=f"rej_{key}_{doc_type}"):
                                        matching_fields = {k: v for k, v in teacher.items() if k in WorkflowState.model_fields}
                                        ws = WorkflowState(**matching_fields)
                                        ws.evaluate_document_approval(doc_type, False)
                                        
                                        if "verified_documents" not in teacher:
                                            teacher["verified_documents"] = []
                                        if filename in teacher["verified_documents"]:
                                            teacher["verified_documents"].remove(filename)
                                            
                                        teacher.update(ws.model_dump())
                                        state["teachers"][key] = teacher
                                        store.save_state(state)
                                        write_log("HR_PORTAL", f"Evaluated document '{filename}' for teacher {key}: approved=False")
                                        st.error(f"Rejected {doc_type}!")
                                        st.rerun()
                
                if not pending_verifications:
                    st.info("No documents pending verification at this moment.")

                    
            elif choice == "Add New Teacher":
                st.markdown("### Add New Teacher")
                st.markdown("Register a new faculty member profile and auto-dispatch welcome email credentials.")
                
                with st.form("add_teacher_form"):
                    email = st.text_input("Email Address", placeholder="e.g. name@pes.edu")
                    name = st.text_input("Full Name", placeholder="e.g. Dr. John Watson")
                    dept = st.text_input("Department", value="Computer Science & Engineering")
                    desig = st.text_input("Designation", value="Assistant Professor")
                    submitted = st.form_submit_button("Register Teacher")
                    
                    if submitted:
                        if not email or not name:
                            st.error("Name and Email are required.")
                        elif email in state.get("teachers", {}):
                            st.error("A teacher with this email already exists.")
                        else:
                            password = secrets.token_urlsafe(10)
                            state["teachers"][email] = {
                                "name": name,
                                "email": email,
                                "department": dept,
                                "designation": desig,
                                "username": email,
                                "password": password,
                                "seating_info": "Not Allotted",
                                "attendance": [
                                    {"date": "2026-06-10", "status": "Absent", "reason": "Personal Leave"},
                                    {"date": "2026-06-20", "status": "Absent", "reason": "Medical Leave"}
                                ],
                                "documents": [],
                                "projects": [],
                                "schedule": [
                                    {"day": "Tuesday", "time": "10:00 AM - 11:30 AM", "class": "CSE-C", "subject": "Database Systems"},
                                    {"day": "Thursday", "time": "02:00 PM - 03:30 PM", "class": "CSE-C", "subject": "Database Systems"}
                                ],
                                "policy_brief": "Pending document upload and policy checker run.",
                                "leave_balance": 30
                            }
                            store.save_state(state)
                            write_log("HR_AGENT", f"New teacher profile created: {email} ({name})")
                            send_welcome_email_task(email=email, username=email, name=name, password=password)
                            st.success(f"Successfully registered {name}! Welcome credentials dispatched to {email}.")
                            st.rerun()

        else:
            st.markdown("## Administration & Chairperson Control Hub")
            
            if choice == "Allot Seating":
                st.markdown("### Allot Seating")
                st.markdown("Assign office rooms and seating arrangements to faculty members.")
                
                teachers_dict = state.get("teachers", {})
                if teachers_dict:
                    teacher_list = list(teachers_dict.keys())
                    selected_teacher_key = st.selectbox("Select Teacher Profile", teacher_list, format_func=lambda x: f"{teachers_dict[x].get('name')} ({x})")
                    current_seating = teachers_dict[selected_teacher_key].get("seating_info", "Not Allotted")
                    st.write(f"Current seat arrangement: `{current_seating}`")
                    
                    new_seating = st.text_input("New Seating Info", placeholder="e.g. Room 405, Desk C")
                    if st.button("Update Seating"):
                        if new_seating:
                            state["teachers"][selected_teacher_key]["seating_info"] = new_seating
                            store.save_state(state)
                            write_log("CHAIRPERSON_AGENT", f"Allotted seating '{new_seating}' for teacher {selected_teacher_key}.")
                            st.success("Seating info updated!")
                            st.rerun()
                else:
                    st.info("No registered teachers to allot seating to.")
                    
            elif choice == "Broadcast Announcement":
                st.markdown("### Broadcast Announcements")
                st.markdown("Publish general notices or instructions to the home bulletin board.")
                
                with st.form("announcement_form"):
                    title = st.text_input("Announcement Title")
                    content = st.text_area("Message Content")
                    sender = st.text_input("Sender Department / Role", value="Admin")
                    submitted = st.form_submit_button("Broadcast Notice")
                    
                    if submitted and title and content:
                        announcements = state.get("announcements", [])
                        new_id = len(announcements) + 1
                        announcements.append({
                            "id": new_id,
                            "title": title,
                            "content": content,
                            "date": datetime.datetime.now().strftime("%Y-%m-%d"),
                            "sender": sender
                        })
                        state["announcements"] = announcements
                        store.save_state(state)
                        write_log("ADMIN_AGENT", f"New announcement published: '{title}'")
                        st.success("Notice broadcasted successfully!")
                        st.rerun()
                        
                st.markdown("#### Published Announcements")
                announcements = state.get("announcements", [])
                if announcements:
                    for ann in reversed(announcements):
                        st.markdown(f"""
                        <div class="glass-card" style="padding:15px; margin-bottom:10px;">
                            <div style="display:flex; justify-content:space-between; margin-bottom:5px;">
                                <strong>{ann.get('title')}</strong>
                                <span style="color:#94a3b8; font-size:0.85em;">{ann.get('date')} | {ann.get('sender')}</span>
                            </div>
                            <p style="margin:0; color:#cbd5e1;">{ann.get('content')}</p>
                        </div>
                        """, unsafe_allow_html=True)
                else:
                    st.info("No announcements found.")
                    
            elif choice == "All Teachers Overview":
                st.markdown("### All Teachers Overview")
                st.markdown("Consolidated status view of all onboarded teachers.")
                teachers_dict = state.get("teachers", {})
                if teachers_dict:
                    overview_data = []
                    for email_key, data in teachers_dict.items():
                        overview_data.append({
                            "Name": data.get("name"),
                            "Email": data.get("email"),
                            "Department": data.get("department"),
                            "Designation": data.get("designation"),
                            "Current Stage": data.get("current_stage", "document_collection"),
                            "Seating": data.get("seating_info", "Not Allotted")
                        })
                    st.table(overview_data)
                else:
                    st.info("No registered teachers.")


# =====================================================================
# 12. DIRECT EXECUTION FALLBACK
# =====================================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
