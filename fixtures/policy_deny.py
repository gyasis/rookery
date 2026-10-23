"""Test policy (T001). Denies an inbound body that contains DENY-ME, with reason fixture-deny. Load it with
ROOKERY_SECURITY=fixtures.policy_deny:DenyPolicy. Reset security._POLICY before and after a test that uses it."""
import security


class DenyPolicy(security.SecurityPolicy):
    def inspect_inbound(self, sender, recipient, body):
        if "DENY-ME" in (body or ""):
            return security.Decision(False, "fixture-deny")
        return security.ALLOW
