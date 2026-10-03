"""If a rule is broken in reviewer.domain.models, raise DomainValidationError, 
so an object that exists is always valid."""
class DomainError(Exception):
    """Base class for every error raised by the domain layer."""


class DomainValidationError(DomainError, ValueError):
    """A domain object was constructed in a state the domain does not allow."""
