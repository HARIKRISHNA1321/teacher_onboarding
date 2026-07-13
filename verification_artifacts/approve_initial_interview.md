# Verification Artifact: initial_interview

- **API Action**: Email HR & Candidate Interview Confirmation
- **Interrupt ID**: `approve_initial_interview`

### State context:
```json
{
  "api_action": "Email HR & Candidate Interview Confirmation",
  "target_node": "initial_interview",
  "state_at_trigger": {
    "preferred_language": "English",
    "visit_count": "1",
    "candidate_name": "Dr. Jane Doe",
    "active_stage": "Credentials-Sent",
    "confirmation_email_sent": "False",
    "credentials_sent": "True",
    "documents": "['file-example_PDF_500_kB.pdf', 'file-sample_150kB.pdf']",
    "policy_brief": "[Pinecone Search (Simulation)] RETRIEVED RULES CONTEXT:\n- Data Input (PII Scrubbed): file-example_PDF_1MB.pdf\n- Joining guidelines: Submit original verification documents within 30 days.\n- Campus ethics: Absolute professionalism in research and teaching duties.",
    "manager_interview_scheduled": "False",
    "chairperson_notified": "False",
    "final_approval_flag": "False",
    "allotment_criteria": "{}",
    "it_notified": "False",
    "admin_notified": "False",
    "leave_balance": "30",
    "email": "1321harikrishna@gmail.com",
    "username": "teacher",
    "password": "password",
    "document_statuses": "{'aadhaar_card': 'approved', 'appointment_letter': 'approved', 'teacher_eligibility_test': 'rejected'}",
    "document_paths": "{'aadhaar_card': 'file-example_PDF_500_kB.pdf', 'appointment_letter': 'file-sample_150kB.pdf', 'teacher_eligibility_test': ''}",
    "pending_tally": "0",
    "current_stage": "document_collection"
  }
}
```

Please approve this action by resuming with: `{"approved": true}`.
