"""User-facing names for PanDoc's compute routes.

The values are intentionally unchanged: they are saved in Streamlit
session state and consumed by the established routing code in app.py.
The UI labels describe the real execution location, not unverifiable
speed, memory, or uptime claims.
"""

STREAMLIT = "Computer A · Fast"
KAGGLE = "Computer B · High capacity"
GITHUB_ACTIONS = "Computer C · Backup"

COMPUTER_D = "Computer D · Tailscale Vina"

OPTIONS = (STREAMLIT, KAGGLE, GITHUB_ACTIONS, COMPUTER_D)

LABELS = {
    STREAMLIT: "This app (default)",
    KAGGLE: "Kaggle worker",
    GITHUB_ACTIONS: "GitHub Actions",
    COMPUTER_D: "Windows Vina worker",
}

RUNTIME_LABELS = {
    STREAMLIT: "Computer A · This app",
    KAGGLE: "Computer B · Kaggle",
    GITHUB_ACTIONS: "Computer C · GitHub Actions",
    COMPUTER_D: "Computer D · Tailscale Vina",
}

DESCRIPTIONS = {
    STREAMLIT: "Runs work in the current Streamlit session. May fall back to GitHub Actions if configured.",
    KAGGLE: "Runs jobs on an external Kaggle worker. Requires a configured, reachable worker; may fall back to GitHub Actions.",
    GITHUB_ACTIONS: "Runs jobs remotely in GitHub Actions. Requires administrator-configured credentials.",
    COMPUTER_D: "Docking-only Windows Vina worker using encrypted outbound GitHub transport.",
}


def display_name(value: str) -> str:
    """Show the provider without changing the saved compute option."""
    return LABELS.get(value, str(value))


def description(value: str) -> str:
    return DESCRIPTIONS.get(value, "")


def runtime_label(value: str) -> str:
    """Compact labels for the top-right runtime selector."""
    return RUNTIME_LABELS.get(value, str(value))
