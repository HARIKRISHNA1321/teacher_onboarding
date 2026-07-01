# Verification Artifact: credential_agent

- **API Action**: Generate & dispatch secure portal credentials via SMTP
- **Interrupt ID**: `approve_credential_agent`

### State context:
```json
{
  "api_action": "Generate & dispatch secure portal credentials via SMTP",
  "target_node": "credential_agent",
  "state_at_trigger": {
    "__session_metadata__": "{'displayName': 'hello'}",
    "confirmation_email_sent": "True",
    "active_stage": "Procedures-Initiated",
    "credentials_sent": "True",
    "manager_interview_scheduled": "True",
    "chairperson_notified": "True",
    "policy_brief": "University Rules Summary Brief:\n- Joining guidelines: Submit original verification documents within 30 days.\n- Campus ethics: Absolute professionalism in research and teaching duties.",
    "it_notified": "True",
    "admin_notified": "True",
    "leave_balance": "22",
    "documents": "['joining_letter.pdf', 'degree.docx']"
  }
}
```

Please approve this action by resuming with: `{"approved": true}`.
