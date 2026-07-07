# Verification Artifact: follow_up_provisioning

- **API Action**: Notify IT and Administrative departments for campus provisioning
- **Interrupt ID**: `approve_follow_up_provisioning`

### State context:
```json
{
  "api_action": "Notify IT and Administrative departments for campus provisioning",
  "target_node": "follow_up_provisioning",
  "state_at_trigger": {
    "__session_metadata__": "{'displayName': 'hello'}",
    "confirmation_email_sent": "True",
    "active_stage": "Policy-Checked",
    "credentials_sent": "True",
    "manager_interview_scheduled": "True",
    "chairperson_notified": "True",
    "policy_brief": "[Pinecone Search @ your_pinecone_environment_here] RETRIEVED RULES CONTEXT:\n- Data Input (PII Scrubbed): joining.pdf, structural.pdf\n- Joining guidelines: Submit original verification documents within 30 days.\n- Campus ethics: Absolute professionalism in research and teaching duties.",
    "it_notified": "True",
    "admin_notified": "True",
    "leave_balance": "22",
    "documents": "['joining.pdf', 'structural.pdf']"
  }
}
```

Please approve this action by resuming with: `{"approved": true}`.
