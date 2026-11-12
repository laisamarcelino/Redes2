from dataclasses import dataclass, replace
from enum import Enum
import threading
from typing import Optional

__all__ = ["Status", "ServerEntry", "ServerTable"]

class Status(Enum):
    UP = "UP"
    DOWN = "DOWN"

@dataclass
class ServerEntry:
    id: str
    ip: str
    port: int
    status: Status = Status.DOWN # Começa DOWN até o primeiro health check bem-sucedido
    active_connections: int = 0
    total_connections: int = 0

    def copy(self) -> "ServerEntry":
        """Retorna uma cópia independente deste registro."""
        return replace(self)

    @property
    def address(self) -> str:
        return f"{self.ip}:{self.port}"


class ServerTable:
    """
    Tabela de servidores compartilhada e thread-safe.
    Mantém o estado operacional, métricas de conexões e executa as políticas
    de seleção de servidores para o Load Balancer de forma atômica.
    """

    def __init__(self, servers: list[ServerEntry]):
        # Armazena os servidores indexados pelo identificador único (ID)
        self._servers: dict[str, ServerEntry] = {s.id: s.copy() for s in servers}
        self._lock = threading.Lock()        # Protege todos os campos mutáveis contra condições de corrida
        self._rr_index = 0                   # Ponteiro circular do Round-Robin
        self._total_global = 0               # Total de conexões mantidas globalmente pelo LB

    def set_status(self, server_id: str, new_status: Status) -> tuple[bool, Optional[Status]]:
        """
        Atualiza o status de um servidor (UP ou DOWN).
        Retorna uma tupla (mudou: bool, status_anterior: Optional[Status]).
        """
        with self._lock:
            server = self._servers.get(server_id)
            if not server:
                return False, None
            
            old_status = server.status
            changed = (old_status != new_status)
            server.status = new_status
            return changed, old_status

    def get_status(self, server_id: str) -> Optional[Status]:
        """Consulta o status atual de um servidor."""
        with self._lock:
            server = self._servers.get(server_id)
            return server.status if server else None

    def increment_connection(self, server_id: str) -> bool:
        """
        Incrementa as conexões ativas e o total histórico de um servidor,
        além do contador global mantido pelo LB.
        """
        with self._lock:
            server = self._servers.get(server_id)
            if not server:
                return False
            server.active_connections += 1
            server.total_connections += 1
            self._total_global += 1
            return True

    def decrement_connection(self, server_id: str) -> bool:
        """
        Decrementa a quantidade de conexões ativas de um servidor (com piso em 0).
        """
        with self._lock:
            server = self._servers.get(server_id)
            if not server:
                return False
            if server.active_connections > 0:
                server.active_connections -= 1
            return True

    def get_server(self, server_id: str) -> Optional[ServerEntry]:
        """Retorna uma cópia segura dos dados de um servidor específico."""
        with self._lock:
            server = self._servers.get(server_id)
            return server.copy() if server else None

    def get_all_servers(self) -> list[ServerEntry]:
        """Retorna cópias seguras de todos os servidores cadastrados."""
        with self._lock:
            return [s.copy() for s in self._servers.values()]

    def get_active_servers(self) -> list[ServerEntry]:
        """Retorna cópias seguras de todos os servidores com status UP."""
        with self._lock:
            return [s.copy() for s in self._servers.values() if s.status == Status.UP]

    def get_total_global(self) -> int:
        """Retorna o número total acumulado de conexões gerenciadas pelo LB."""
        with self._lock:
            return self._total_global

    def get_total_active(self) -> int:
        """Retorna a soma das conexões ativas em todos os servidores no momento."""
        with self._lock:
            return sum(s.active_connections for s in self._servers.values())

    def select_round_robin(self) -> Optional[ServerEntry]:
        """
        Seleciona o próximo servidor disponível (UP) usando a política Round-Robin circular.
        Garante avanço atômico do ponteiro circular.
        """
        with self._lock:
            # Lista estável e ordenada por ID de servidores disponíveis
            up_servers = [s for s in self._servers.values() if s.status == Status.UP]
            if not up_servers:
                return None

            up_servers.sort(key=lambda s: s.id)
            chosen = up_servers[self._rr_index % len(up_servers)]
            self._rr_index = (self._rr_index + 1) % len(up_servers)
            return chosen.copy()

    def select_least_connections(self) -> Optional[ServerEntry]:
        """
        Seleciona o servidor disponível (UP) com menor número de conexões ativas.
        Em caso de empate, utiliza o ID do servidor como desempate estável.
        """
        with self._lock:
            up_servers = [s for s in self._servers.values() if s.status == Status.UP]
            if not up_servers:
                return None

            # Desempate estável: menor número de conexões ativas, depois menor ID
            chosen = min(up_servers, key=lambda s: (s.active_connections, s.id))
            return chosen.copy()

    def select_server(self, policy: str) -> Optional[ServerEntry]:
        """
        Despacha a seleção conforme a política configurada:
        'round-robin' ou 'least-connections'.
        """
        if policy == "round-robin":
            return self.select_round_robin()
        elif policy == "least-connections":
            return self.select_least_connections()
        else:
            raise ValueError(f"Política desconhecida: {policy}")

    def get_snapshot(self) -> dict:
        """Retorna um snapshot estruturado do estado da tabela."""
        with self._lock:
            return {
                "total_global": self._total_global,
                "total_active": sum(s.active_connections for s in self._servers.values()),
                "servers": [
                    {
                        "id": s.id,
                        "ip": s.ip,
                        "port": s.port,
                        "status": s.status.value,
                        "active_connections": s.active_connections,
                        "total_connections": s.total_connections,
                    }
                    for s in self._servers.values()
                ],
            }

    def format_table(self) -> str:
        """Formata o estado atual da tabela em formato tabular para logs e depuração."""
        snapshot = self.get_snapshot()
        lines = [
            "+---------+----------------------+--------+-----------+-------+",
            "| ID      | Endereço             | Status | Ativas    | Total |",
            "+---------+----------------------+--------+-----------+-------+",
        ]
        for s in snapshot["servers"]:
            addr = f"{s['ip']}:{s['port']}"
            lines.append(
                f"| {s['id']:<7} | {addr:<20} | {s['status']:<6} | {s['active_connections']:<9} | {s['total_connections']:<5} |"
            )
        lines.append("+---------+----------------------+--------+-----------+-------+")
        lines.append(f"Total global mantido pelo LB: {snapshot['total_global']} conexões | Ativas agora: {snapshot['total_active']}")
        return "\n".join(lines)

    def select_and_reserve(self, policy: str):
        '''
        Função genérica para receber um servidor baseado na politíca.
        Escolhe o servidor e já incrementa o valor de conexões
        Vai servir para evitar janela de corrida

        Retorna um ServerEntry, cópia do servidor escolhido
        '''
        with self._lock:
            ups = [s for s in self._servers.values() if s.status == Status.UP]

            if not ups:
                return None

            if policy == "round-robin":
                ups.sort(key=lambda s: s.id)
                chosen = ups[self._rr_index % len(ups)]
                self._rr_index = (self._rr_index + 1) % len(ups)
            else:  # least-connections
                chosen = min(ups, key=lambda s: (s.active_connections, s.id))

            chosen.active_connections += 1
            chosen.total_connections += 1
            self._total_global += 1
            return chosen.copy()
