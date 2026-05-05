"""
Credential Management for AI Weekly Newsletter.

Handles retrieval of sensitive credentials from multiple sources with a fallback chain:
1. Anthropic Managed Credentials (when available in Managed Agents environment)
2. Environment variables (from .env or system env)
3. Secure vault (placeholder for future AWS Secrets Manager, etc.)

Inspired by "Scaling Managed Agents: Decoupling the brain from the hands" (Anthropic, Apr 2026)
which recommends keeping secrets outside the agent/orchestrator execution environment.
"""

import os
from typing import Optional, Dict, Any
from dotenv import load_dotenv

load_dotenv(override=True)


class CredentialManager:
    """
    Centralized credential management with fallback chain.
    Supports Anthropic vault, environment variables, and secure vaults.
    """

    def __init__(self):
        self.cache: Dict[str, str] = {}
        self.anthropic_vault_available = False
        # TODO: Check if running in Managed Agents environment
        # This would set self.anthropic_vault_available = True

    def get_credential(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """
        Retrieve a credential with fallback chain.

        Args:
            key: Credential key (e.g., 'SMTP_PASSWORD', 'SMTP_USER')
            default: Default value if not found anywhere

        Returns:
            Credential value or default
        """
        # Check cache first
        if key in self.cache:
            return self.cache[key]

        # Try Anthropic vault (when available)
        if self.anthropic_vault_available:
            value = self._get_from_anthropic_vault(key)
            if value:
                self.cache[key] = value
                return value

        # Try environment variables
        value = os.getenv(key)
        if value:
            self.cache[key] = value
            return value

        # Return default
        if default:
            self.cache[key] = default
            return default

        raise ValueError(f"Credential not found: {key}")

    @staticmethod
    def _get_from_anthropic_vault(key: str) -> Optional[str]:
        """
        Retrieve credential from Anthropic Managed Credentials vault.

        This is a placeholder for future implementation once Anthropic's
        Managed Credentials API is available.

        When available, this would be called like:
        ```
        from anthropic.vault import retrieve_credential
        return retrieve_credential(key)
        ```
        """
        # TODO: Implement once Anthropic vault API is available
        # For now, return None to fall back to environment variables
        return None

    def get_smtp_credentials(self) -> Dict[str, str]:
        """
        Retrieve all SMTP credentials with sensible defaults.

        Returns:
            Dict with 'host', 'port', 'user', 'password', 'from_addr'
        """
        return {
            "host": self.get_credential("SMTP_HOST", "smtp.gmail.com"),
            "port": self.get_credential("SMTP_PORT", "587"),
            "user": self.get_credential("SMTP_USER"),  # Required
            "password": self.get_credential("SMTP_PASSWORD"),  # Required
            "from_addr": self.get_credential("SMTP_FROM", None),  # Optional, defaults to user
        }

    def validate_smtp_credentials(self) -> bool:
        """
        Validate that all required SMTP credentials are available.

        Returns:
            True if credentials are valid, False otherwise
        """
        try:
            creds = self.get_smtp_credentials()
            return creds["user"] is not None and creds["password"] is not None
        except ValueError:
            return False


# Global instance
_credential_manager: Optional[CredentialManager] = None


def get_credential_manager() -> CredentialManager:
    """Get the global credential manager instance."""
    global _credential_manager
    if _credential_manager is None:
        _credential_manager = CredentialManager()
    return _credential_manager
