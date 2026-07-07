from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Dict, Optional, List
import json
import os
import datetime
from app.core.privacy import DataMaskingMiddleware
from app.core.local_storage import LocalStateStore

router = APIRouter()

class ChatRequest(BaseModel):
    message: str

class ActionRequest(BaseModel):
    action: str  # e.g., "approve_interview", "upload_documents", "schedule", "allotment", "provision"
    payload: Optional[Any] = None

@router.get("/health")
def health_check() -> dict:
    """Production health check endpoint for Render service verification."""
    return {"status": "healthy"}

@router.post("/webhook/upload")
def webhook_upload(payload: dict) -> dict:
    """Webhook endpoint to handle incoming file metadata with strict PII scrubbing."""
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
    from dotenv import load_dotenv
    load_dotenv(override=True)
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return user_input

    refiner_prompt = (
        "You are an expert Query Refiner for the PESU HR & Policy RAG system.\n"
        "Your goal is to transform vague or conversational user questions into precise search queries that will maximize the retrieval of accurate policy information from our Pinecone database.\n\n"
        "### Guidelines for Refinement:\n"
        "1. Identify the core intent of the user's question (e.g., if they ask \"What leaves can I take?\", map this to keywords like \"Leave types\", \"Privilege Leave\", \"Sick Leave\", \"Policy\").\n"
        "2. Do not answer the question; only rewrite it to be optimal for vector search.\n"
        "3. If the user uses colloquial language, translate it into standard HR/Institutional terminology.\n"
        "4. If the query is already precise, keep it as is.\n\n"
        "### Examples:\n"
        "- Input: \"tell me about the types of leaves available in pesu\"\n"
        "- Refined Query: \"What are the different types of leaves, including Privilege Leave, available under PESU HR policy?\"\n\n"
        "- Input: \"how do I get leave for vacation?\"\n"
        "- Refined Query: \"What is the procedure and approval process for availing Privilege Leave at PESU?\"\n\n"
        f"User Input: \"{user_input}\"\n"
        "Refined Query:"
    )

    try:
        import requests
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        data = {
            "contents": [{"parts": [{"text": refiner_prompt}]}]
        }
        response = requests.post(url, headers=headers, json=data, timeout=15.0)
        if response.status_code == 200:
            res_json = response.json()
            refined = res_json["candidates"][0]["content"]["parts"][0]["text"].strip()
            if refined.startswith('"') and refined.endswith('"'):
                refined = refined[1:-1].strip()
            write_log("QUERY_REFINER", f"Refined '{user_input}' -> '{refined}'")
            return refined
    except Exception as e:
        write_log("QUERY_REFINER_ERROR", f"Failed to refine query: {str(e)}")

    return user_input

@router.post("/api/chat")
def chatbot_endpoint(req: ChatRequest) -> dict:
    # Read the current query
    clean_input = DataMaskingMiddleware.redact_pii(req.message)
    write_log("CHATBOT_AGENT", f"Received message: '{clean_input}'")
    
    # 1. Refine query before calling Pinecone RAG search
    refined_query = refine_query_with_gemini(clean_input)
    
    # 2. Query Pinecone database to get context
    from app.tools.pinecone_rag import PineconeRAGService
    pinecone_service = PineconeRAGService()
    rules_context = pinecone_service.query_rules(refined_query)
    
    # 2. Call Gemini model using API Key
    from dotenv import load_dotenv
    load_dotenv(override=True)
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    
    prompt = (
        f"You are a helpful PES Onboarding Assistant. Use the following Pinecone RAG context to answer the user's query.\n"
        f"If you answer using the retrieved context guidelines, always append '[Source: Pinecone Database]' to make it clear that the response refers to retrieved records.\n"
        f"If the context does not contain enough info to answer the query, reply to the best of your knowledge, specify that it is general info, and do not append the citation.\n\n"
        f"Context:\n{rules_context}\n\n"
        f"User Query: {clean_input}\n\n"
        f"Response:"
    )
    
    answer = None
    if api_key:
        try:
            import requests
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            headers = {"Content-Type": "application/json"}
            data = {
                "contents": [{"parts": [{"text": prompt}]}]
            }
            import time
            retries = 3
            backoff = 0.5
            for attempt in range(retries):
                response = requests.post(url, headers=headers, json=data, timeout=15.0)
                if response.status_code == 200:
                    res_json = response.json()
                    answer = res_json["candidates"][0]["content"]["parts"][0]["text"]
                    break
                elif response.status_code == 503 and attempt < retries - 1:
                    time.sleep(backoff)
                    backoff *= 2
                else:
                    write_log("CHATBOT_ERROR", f"Gemini API returned status code {response.status_code}: {response.text}")
                    break
        except Exception as e:
            write_log("CHATBOT_ERROR", f"Failed to contact Gemini API: {str(e)}")

    if not answer:
        # Fallback if Gemini key is invalid/missing or API request failed/timed out
        answer = f"[RAG Rules Context] Retrieved Rules:\n{rules_context}\n\n(Please check that GEMINI_API_KEY in .env is valid)"
    
    return {"response": answer}

@router.post("/api/action")
def trigger_action(req: ActionRequest) -> dict:
    store = LocalStateStore()
    state = store.load_state()
    if not state or "teachers" not in state:
        state = initialize_default_state()

    action = req.action
    payload = req.payload

    if action == "add_teacher":
        username = payload.get("username")
        if username in state["teachers"]:
            raise HTTPException(status_code=400, detail="Teacher username already exists.")
        
        state["teachers"][username] = {
            "name": payload.get("name"),
            "email": payload.get("email"),
            "department": payload.get("department", "CSE"),
            "designation": payload.get("designation", "Assistant Professor"),
            "username": username,
            "password": payload.get("password", "password"),
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
        write_log("HR_AGENT", f"New teacher profile created: {username} ({payload.get('name')})")

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
        if username not in state["teachers"]:
            raise HTTPException(status_code=404, detail="Teacher not found.")
        
        if doc_name not in state["teachers"][username]["documents"]:
            state["teachers"][username]["documents"].append(doc_name)
        
        # Auto-update Pinecone check brief
        from app.tools.pinecone_rag import PineconeRAGService
        pinecone_service = PineconeRAGService()
        brief = pinecone_service.query_rules(doc_name)
        state["teachers"][username]["policy_brief"] = brief
        write_log("CANDIDATE_PORTAL", f"Uploaded document: {doc_name} for teacher {username}")

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


