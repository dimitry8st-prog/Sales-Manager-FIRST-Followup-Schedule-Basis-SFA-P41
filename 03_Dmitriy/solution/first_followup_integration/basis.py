"""SFA-P41-BASIS-R1 implementation target. No I/O or state ownership."""
class ScheduleBasisError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)

def project_schedule_basis(*, scope, policy_version, source_action_id, state, policy,
                           action, attempt, receipt, now):
    raise NotImplementedError("MISSING_IMPLEMENTATION")
