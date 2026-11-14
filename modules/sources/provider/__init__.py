"""The socket of external source providers (contracts/provider_socket.md): the client, the site and the player of a provider
as the registries of the module see them, the connector that keeps them in step with the provider's manifest, and the gate that
says when nothing of the provider's world may be begun."""
from .client import ProviderClient, ProviderError
from .connector import MANIFEST_TOPIC, STATE_TOPIC, Connector
from .host import CHANGED_TOPIC, ProviderHost
from .gate import NOT_CONNECTED, belongs_to_provider, block, is_web_address, not_connected
from .remote import RemoteFamily, RemoteSite

__all__ = ["CHANGED_TOPIC", "ProviderHost", "Connector", "ProviderClient", "ProviderError", "RemoteFamily", "RemoteSite", "MANIFEST_TOPIC", "STATE_TOPIC",
           "NOT_CONNECTED", "belongs_to_provider", "block", "is_web_address", "not_connected"]
