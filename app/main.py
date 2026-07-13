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
import asyncio
import datetime
import secrets
import json
from typing import Dict, Any, List, Optional
import google.auth
import streamlit as st
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from google.adk.cli.fast_api import get_fast_api_app
from google.cloud import logging as google_cloud_logging

from app.app_utils.telemetry import setup_telemetry
from app.app_utils.typing import Feedback
from app.endpoints.routes import (
    router,
    send_welcome_email_task,
    send_verification_email_task,
    refine_query_with_gemini,
    write_log,
)
from app.core.local_storage import LocalStateStore
from app.core.privacy import DataMaskingMiddleware
from app.core.agent import WorkflowState
from app.tools.pinecone_rag import PineconeRAGService

# ==========================================
# 1. FastAPI App Initialization & Setup
# ==========================================
setup_telemetry()
otel_to_cloud = True
try:
    _, project_id = google.auth.default()
except Exception:
    project_id = "mock-project-id"
    otel_to_cloud = False

try:
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

# Include endpoints router
app.include_router(router)

# Remove default root route redirection to playground UI
app.router.routes = [r for r in app.router.routes if not hasattr(r, "path") or r.path != "/"]

# Mount static files directory
static_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
def read_index():
    return FileResponse(os.path.join(static_dir, "index.html"))

# Ambient Background Operator definition
async def ambient_background_worker():
    from app.core.local_storage import LocalStateStore
    store = LocalStateStore()
    while True:
        try:
            state = store.load_state()
            # Simulates ambient agent watching database state changes and handling long-running updates
        except Exception:
            pass
        await asyncio.sleep(5)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(ambient_background_worker())

@app.post("/feedback")
def collect_feedback(feedback: Feedback) -> dict[str, str]:
    """Collect and log feedback."""
    logger.log_struct(feedback.model_dump(), severity="INFO")
    return {"status": "success"}

# ==========================================
# 2. Streamlit UI Rendering Block
# ==========================================
if st.runtime.exists():
    # Page Configuration
    st.set_page_config(
        page_title="PES University Academic Portal",
        page_icon="🎓",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    # Set up style overrides for dark mode & premium design
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

        /* Gradient Background Glows */
        .stApp {
            background: radial-gradient(circle at 10% 20%, rgba(15, 23, 42, 1) 0%, rgba(8, 10, 15, 1) 90%);
        }

        /* Custom Glassmorphism Containers */
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

        /* Badges */
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

        /* Header Logo */
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
        
        /* Buttons custom styling */
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
        
        /* Sidebar styling */
        .css-1d391tw {
            background-color: #0f172a !important;
        }
    </style>
    """, unsafe_allow_html=True)

    # State Store Initialization
    store = LocalStateStore()
    state = store.load_state()
    if not state or "teachers" not in state:
        from app.endpoints.routes import initialize_default_state
        state = initialize_default_state()
        store.save_state(state)

    # Session State for Authentication
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
        st.session_state.username = ""
        st.session_state.role = ""
        st.session_state.teacher_key = ""

    # Logout function
    def logout():
        st.session_state.logged_in = False
        st.session_state.username = ""
        st.session_state.role = ""
        st.session_state.teacher_key = ""
        st.rerun()

    # ----------------- LOGIN SCREEN -----------------
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
                    # 1. Admin login
                    if username_input == "admin" and password_input == "password":
                        st.session_state.logged_in = True
                        st.session_state.username = "Administrator"
                        st.session_state.role = "Admin"
                        st.success("Successfully logged in as Admin!")
                        st.rerun()
                    # 2. HR login
                    elif username_input == "hr" and password_input == "password":
                        st.session_state.logged_in = True
                        st.session_state.username = "HR Manager"
                        st.session_state.role = "HR"
                        st.success("Successfully logged in as HR!")
                        st.rerun()
                    # 3. Dynamic teacher login
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

            st.markdown("""
            <div class="glass-card">
                <h4 style="margin-top:0; color:#38bdf8;">Primary Logins for Testing:</h4>
                <p style="margin-bottom:5px;"><strong>Candidate / Teacher:</strong> <code>teacher</code> / <code>password</code></p>
                <p style="margin-bottom:5px;"><strong>HR Dept:</strong> <code>hr</code> / <code>password</code></p>
                <p style="margin-bottom:0;"><strong>Admin:</strong> <code>admin</code> / <code>password</code></p>
            </div>
            """, unsafe_allow_html=True)

    # ----------------- MAIN APP WINDOW -----------------
    else:
        # Sidebar Navigation & Profile
        with st.sidebar:
            st.markdown(f"""
            <div style="text-align: center; margin-bottom: 20px;">
                <div style="font-size: 3rem;">🎓</div>
                <h3 style="margin: 0; background: linear-gradient(to right, #38bdf8, #818cf8); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-weight: 700;">PES UNIVERSITY</h3>
                <span class="badge badge-primary">Role: {st.session_state.role}</span>
            </div>
            """, unsafe_allow_html=True)
            
            # User details card
            st.markdown(f"""
            <div class="glass-card" style="padding: 15px; text-align: center;">
                <div style="font-size: 2.5rem; margin-bottom: 5px;">👤</div>
                <h4 style="margin: 0; font-size: 1.1rem;">{st.session_state.username}</h4>
                <p style="color: #94a3b8; font-size: 0.85rem; margin-top: 2px; margin-bottom: 0;">{st.session_state.role if st.session_state.role != "Teacher" else state["teachers"][st.session_state.teacher_key].get("email")}</p>
            </div>
            """, unsafe_allow_html=True)
            
            # Navigation Options based on Role
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
            else: # Admin
                menu_options = [
                    "Allot Seating",
                    "Broadcast Announcement",
                    "All Teachers Overview"
                ]
                
            choice = st.radio("Navigation", menu_options, label_visibility="collapsed")
            
            st.markdown('<div style="height: 40px;"></div>', unsafe_allow_html=True)
            if st.button("Sign Out", use_container_width=True):
                logout()

        # ----------------- TEACHER VIEW -----------------
        if st.session_state.role == "Teacher":
            teacher_email = st.session_state.teacher_key
            teacher_info = state["teachers"][teacher_email]
            
            # Document validation details sync check
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
                
                # Chat history
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
                    
                    # Call chatbot endpoint logic
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
                            import requests
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
                            # Emulate file saving
                            doc_name = uploaded_file.name
                            
                            from app.core.agent import WorkflowState
                            matching_fields = {k: v for k, v in teacher_info.items() if k in WorkflowState.model_fields}
                            ws = WorkflowState(**matching_fields)
                            
                            if doc_name not in ws.documents:
                                ws.documents.append(doc_name)
                            
                            ws.update_document_upload_path(doc_type_choice, doc_name)
                            
                            # Query Pinecone rules
                            pinecone_service = PineconeRAGService()
                            brief = pinecone_service.query_rules(doc_name)
                            ws.policy_brief = brief
                            
                            # Update state
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

        # ----------------- HR VIEW -----------------
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
                
                teachers_dict = state.get("teachers", {})
                pending_verifications = False
                
                for key, teacher in teachers_dict.items():
                    doc_statuses = teacher.get("document_statuses", {})
                    doc_paths = teacher.get("document_paths", {})
                    
                    # Check if there are pending docs
                    has_pending = any(status == "pending" for status in doc_statuses.values())
                    if has_pending:
                        pending_verifications = True
                        st.markdown(f"#### Teacher: {teacher.get('name')} ({teacher.get('email')})")
                        
                        for doc_type, status in doc_statuses.items():
                            if status == "pending":
                                filename = doc_paths.get(doc_type, "N/A")
                                st.write(f"- **{doc_type.replace('_', ' ').title()}:** File `{filename}` is pending approval.")
                                col1, col2 = st.columns(2)
                                with col1:
                                    if st.button("Approve", key=f"app_{key}_{doc_type}"):
                                        # Call approval logic
                                        from app.core.agent import WorkflowState
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
                                        
                                        # If transitioned to policy_review, send verification email
                                        if ws.current_stage == "policy_review":
                                            send_verification_email_task(email=teacher.get("email"), name=teacher.get("name"))
                                            
                                        st.success(f"Approved {doc_type}!")
                                        st.rerun()
                                with col2:
                                    if st.button("Reject", key=f"rej_{key}_{doc_type}"):
                                        # Call rejection logic
                                        from app.core.agent import WorkflowState
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
                            
                            # Dispatch Welcome Email
                            send_welcome_email_task(email=email, username=email, name=name, password=password)
                            
                            st.success(f"Successfully registered {name}! Welcome credentials dispatched to {email}.")
                            st.rerun()

        # ----------------- ADMIN VIEW -----------------
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

# ==========================================
# 3. Direct execution fallback
# ==========================================
if __name__ == "__main__":
    # If run directly as a python script, run the FastAPI backend server
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
