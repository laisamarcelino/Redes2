from dataclasses import dataclass, field
from enum import Enum
import threading

__all__ = ["Status", "ServerEntry", "ServerTable"]

class Status(Enum):
    UP = "UP"
    DOWN = "DOWN"

@dataclass
class ServerEntry:
    id: str
    ip: str
    port: int
    status: Status = Status.DOWN # comeca DOWN ate o health check
    active_connections: int = 0
    total_connections: int = 0

class ServerTable:
    def __init__(self, servers: list[ServerEntry]):
        self._servers: dict[str, ServerEntry] = {s.id: s for s in servers}
        self._lock = threading.Lock()        # Protege todos os campos mutáveis
        self._rr_index = 0                   # Ponteiro do Round-Robin
        self._total_global = 0               # Total de conexões atendidas pelo LB


