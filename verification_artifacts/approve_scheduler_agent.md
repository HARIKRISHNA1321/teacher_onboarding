# Verification Artifact: scheduler_agent

- **API Action**: Schedule calendar appointment and invite chairperson
- **Interrupt ID**: `approve_scheduler_agent`

### State context:
```json
{
  "api_action": "Schedule calendar appointment and invite chairperson",
  "target_node": "scheduler_agent",
  "state_at_trigger": {
    "leave_balance": "30",
    "email": "1321harikrishna@gmail.com",
    "username": "teacher",
    "password": "password",
    "confirmation_email_sent": "True",
    "active_stage": "Procedures-Initiated"
  }
}
```

Please approve this action by resuming with: `{"approved": true}`.
