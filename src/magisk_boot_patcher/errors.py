"""Exception types mapped to process exit codes."""


class UserError(Exception):
    """Problem caused by the user's input (exit code 1)."""

    exit_code = 1


class ToolError(Exception):
    """Internal error or failing external tool (exit code 2)."""

    exit_code = 2

    def __init__(self, message: str, command: list[str] | None = None, stderr: str = ""):
        super().__init__(message)
        self.command = command or []
        self.stderr = stderr

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.command:
            parts.append("command: " + " ".join(map(str, self.command)))
        if self.stderr:
            parts.append("stderr: " + self.stderr.strip())
        return "\n".join(parts)


class OfflineError(UserError):
    """GitHub unreachable (or rate-limited) and nothing usable in the cache (exit code 1)."""
