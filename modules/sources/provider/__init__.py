"""The socket of external source providers (contracts/provider_socket.md): the client, the site and the player of a provider
as the registries of the module see them, the connector that keeps them in step with the provider's manifest, and the gate that
says when nothing of the provider's world may be begun."""
from .client import ProviderClient, ProviderError
from .connector import Connector
from .host import ProviderHost
from .gate import belongs_to_provider, block, not_connected
from .remote import RemoteFamily, RemoteSite

__all__ = ["ProviderHost", "Connector", "ProviderClient", "ProviderError", "RemoteFamily", "RemoteSite",
           "belongs_to_provider", "block", "not_connected"]
