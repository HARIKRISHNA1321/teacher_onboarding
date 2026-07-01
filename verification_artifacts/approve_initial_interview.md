# Verification Artifact: initial_interview

- **API Action**: Email HR & Candidate Interview Confirmation
- **Interrupt ID**: `approve_initial_interview`

### State context:
```json
{
  "api_action": "Email HR & Candidate Interview Confirmation",
  "target_node": "initial_interview",
  "state_at_trigger": {
    "__session_metadata__": "{'displayName': 'hello'}",
    "confirmation_email_sent": "True",
    "active_stage": "Policy-Checked",
    "credentials_sent": "True",
    "manager_interview_scheduled": "True",
    "chairperson_notified": "True",
    "policy_brief": "[Qdrant Search @ http://localhost:6333] RETRIEVED RULES CONTEXT:\n- Data Input (PII Scrubbed): done\n- Joining guidelines: Submit original verification documents within 30 days.\n- Campus ethics: Absolute professionalism in research and teaching duties.",
    "it_notified": "True",
    "admin_notified": "True",
    "leave_balance": "22",
    "documents": "['done']"
  }
}
```

Please approve this action by resuming with: `{"approved": true}`.
