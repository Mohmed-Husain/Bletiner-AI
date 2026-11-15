"""Unified exception hierarchy (DOC-14 section 4.3)."""

from __future__ import annotations


class BletinerError(Exception):
    """Base class for every Bletiner error."""


class ConfigurationError(BletinerError):
    pass


# --- storage -------------------------------------------------------------------------------------------
class StorageError(BletinerError):
    pass


class MigrationError(StorageError):
    pass


class RecordNotFoundError(StorageError):
    pass


class IntegrityViolationError(StorageError):
    """Foreign-key / check-constraint violation surfaced as a typed error."""


# --- scope ---------------------------------------------------------------------------------------------
class ScopeViolationError(BletinerError):
    pass


class OutOfScopeTargetError(ScopeViolationError):
    pass


class PrivateNetworkBlockedError(ScopeViolationError):
    pass


class SharedCDNTenantBlockedError(ScopeViolationError):
    pass


class DNSRebindingDetectedError(ScopeViolationError):
    pass


class RedirectOutOfScopeError(ScopeViolationError):
    """Layer A redirect hook rejected a ``Location`` header (``REDIRECT_OUT_OF_SCOPE``)."""


# --- containers ----------------------------------------------------------------------------------------
class ContainerError(BletinerError):
    pass


class DockerDaemonUnavailableError(ContainerError):
    pass


class ContainerCreationError(ContainerError):
    pass


class ContainerTimeoutError(ContainerError):
    pass


class ContainerOOMError(ContainerError):
    pass


class InsecureContainerConfigError(ContainerError):
    """A container configuration violates the hardening policy (e.g. NET_ADMIN, privileged, docker.sock)."""


class UnsafeCommandError(ContainerError):
    """A command was not a structured argv list or contained forbidden constructs."""


# --- gateway -------------------------------------------------------------------------------------------
class GatewayError(BletinerError):
    pass


class GatewayCrashError(GatewayError):
    pass


class GatewayNotReadyError(GatewayError):
    pass


class SNIInspectionFailureError(GatewayError):
    pass


class NetnsRouteInjectionError(GatewayError):
    pass


class SubnetPoolExhaustedError(GatewayError):
    pass


class PoCVerificationFailedError(GatewayError):
    pass


# --- providers -----------------------------------------------------------------------------------------
class ProviderError(BletinerError):
    pass


class AuthenticationError(ProviderError):
    pass


class RateLimitExceededError(ProviderError):
    pass


class ContextLengthExceededError(ProviderError):
    pass


class ModelUnavailableError(ProviderError):
    pass


# --- agent harness -------------------------------------------------------------------------------------
class AgentHarnessError(BletinerError):
    pass


class LoopBudgetExceededError(AgentHarnessError):
    pass


class CheckpointCorruptedError(AgentHarnessError):
    pass


class SteeringValidationError(AgentHarnessError):
    pass


# --- policy --------------------------------------------------------------------------------------------
class PolicyViolationError(BletinerError):
    pass


class DestructiveActionBlockedError(PolicyViolationError):
    pass


class Tier4ToolBlockedError(PolicyViolationError):
    pass
