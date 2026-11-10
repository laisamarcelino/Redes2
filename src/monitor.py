import argparse
import socket
import subprocess
import sys
import threading
import time
from typing import Callable, Optional

try:
    from server_table import ServerEntry, ServerTable, Status
except ImportError:
    from src.server_table import ServerEntry, ServerTable, Status

__all__ = ["HealthMonitor"]

def probe_server(ip: str, port: int, timeout: float = 1.0) -> bool:
    """
    Tenta abrir uma conexão TCP rápida com o servidor no IP:PORTA especificado.
    Retorna True se a conexão foi estabelecida com sucesso; False caso contrário.
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((ip, port))
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False
    finally:
        if sock:
            try:
                sock.close()
            except OSError:
                pass


class HealthMonitor:
    """
    Sistema de Monitoramento Periódico de Integridade (Health Check).
    Executa verificações periódicas nos servidores da ServerTable,
    detectando falhas (UP -> DOWN) e recuperações (DOWN -> UP).
    Opcionalmente, pode executar rotina de auto-recuperação de servidores caídos.
    """

    def __init__(
        self,
        server_table: ServerTable,
        interval: float = 2.0,
        timeout: float = 1.0,
        auto_recover: bool = False,
        on_status_change: Optional[Callable[[ServerEntry, Status, Status], None]] = None,
    ):
        self.server_table = server_table
        self.interval = interval
        self.timeout = timeout
        self.auto_recover = auto_recover
        self.on_status_change = on_status_change

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Rastreia processos reiniciados automaticamente pelo monitor
        self._recovered_processes: dict[str, subprocess.Popen] = {}
        self._last_restart_attempt: dict[str, float] = {}

    def check_server(self, server: ServerEntry) -> bool:
        """
        Executa o health check em um servidor individual e atualiza a ServerTable.
        Retorna True se o servidor estiver responsivo.
        """
        is_alive = probe_server(server.ip, server.port, self.timeout)
        new_status = Status.UP if is_alive else Status.DOWN

        changed, old_status = self.server_table.set_status(server.id, new_status)

        if changed:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            if new_status == Status.DOWN:
                print(
                    f"[{timestamp}] [MONITOR] [FALHA DETECTADA] Servidor '{server.id}' "
                    f"({server.ip}:{server.port}) FALHOU! Status alterado: UP -> DOWN",
                    flush=True,
                )
                if self.auto_recover:
                    self._attempt_auto_recovery(server)
            else:
                print(
                    f"[{timestamp}] [MONITOR] [RECUPERAÇÃO] Servidor '{server.id}' "
                    f"({server.ip}:{server.port}) RECUPERADO! Status alterado: DOWN -> UP",
                    flush=True,
                )

            if self.on_status_change and old_status is not None:
                try:
                    self.on_status_change(server, old_status, new_status)
                except Exception as e:
                    print(f"[MONITOR] Erro ao executar callback on_status_change: {e}", flush=True)

        return is_alive

    def _attempt_auto_recovery(self, server: ServerEntry):
        """
        Estratégia de recuperação automática: reinicia o processo backend caso ele tenha falhado
        Evita relançamento em loop respeitando um tempo mínimo entre tentativas
        """
        now = time.time()
        last_attempt = self._last_restart_attempt.get(server.id, 0)
        if now - last_attempt < 3.0:
            return

        self._last_restart_attempt[server.id] = now
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(
            f"[{timestamp}] [MONITOR] [AUTO-RECUPERAÇÃO] Reiniciando processo para '{server.id}' "
            f"em {server.ip}:{server.port}...",
            flush=True,
        )

        try:
            # Roda python3 src/server.py --host IP --port PORT --id ID
            cmd = [
                sys.executable,
                "src/server.py",
                "--host",
                server.ip,
                "--port",
                str(server.port),
                "--id",
                server.id,
            ]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._recovered_processes[server.id] = proc
            print(
                f"[{timestamp}] [MONITOR] [AUTO-RECUPERAÇÃO] Servidor '{server.id}' "
                f"relançado com PID {proc.pid}.",
                flush=True,
            )
        except Exception as e:
            print(f"[MONITOR] Falha ao tentar auto-recuperar '{server.id}': {e}", flush=True)

    def check_all_once(self, log_initial: bool = False) -> dict[str, bool]:
        """
        Executa uma rodada imediata e síncrona de checagem em todos os servidores.
        Útil para inicializar o estado da tabela antes do LB aceitar conexões.
        Se log_initial=True, exibe o diagnóstico de cada servidor mesmo sem alteração de status.
        """
        results = {}
        for server in self.server_table.get_all_servers():
            alive = self.check_server(server)
            results[server.id] = alive
            if log_initial:
                status_str = "UP (Conectado)" if alive else "DOWN (Inacessível / Desligado)"
                print(f"[MONITOR] Servidor registrado: '{server.id}' ({server.ip}:{server.port}) -> {status_str}", flush=True)
        return results

    def _run_loop(self):
        """Loop contínuo de monitoramento executado na thread de monitoramento."""
        while not self._stop_event.is_set():
            self._stop_event.wait(self.interval)
            if not self._stop_event.is_set():
                self.check_all_once()

    def start(self):
        """Inicia a thread de monitoramento em segundo plano."""
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        print(f"[MONITOR] Iniciando verificação de integridade (intervalo: {self.interval}s, timeout: {self.timeout}s)...", flush=True)
        # Realiza uma checagem inicial explícita de todos os servidores cadastrados
        self.check_all_once(log_initial=True)

        self._thread = threading.Thread(
            target=self._run_loop,
            name="HealthMonitorThread",
            daemon=True,
        )
        self._thread.start()
        print("[MONITOR] Monitoramento em segundo plano ativo.\n", flush=True)

    def stop(self):
        """Sinaliza parada para a thread de monitoramento e encerra processos auto-recuperados."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=self.timeout + 1.0)

        # Encerra processos criados por auto-recuperação ao desligar o monitor
        for s_id, proc in self._recovered_processes.items():
            if proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass

        print("[MONITOR] Monitoramento encerrado.", flush=True)


def parse_args():
    p = argparse.ArgumentParser(description="Sistema de Monitoramento Standalone de Servidores")
    p.add_argument(
        "--servers",
        nargs="+",
        required=True,
        metavar="IP:PORTA",
        help="Lista de servidores no formato IP:PORTA",
    )
    p.add_argument("--interval", type=float, default=2.0, help="Intervalo entre checagens em segundos (default: 2.0)")
    p.add_argument("--timeout", type=float, default=1.0, help="Timeout de conexão TCP em segundos (default: 1.0)")
    p.add_argument("--auto-recover", action="store_true", help="Ativa reinício automático de servidores que falharem")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    
    # Processa os servidores permitindo tanto espaço quanto vírgula
    raw_servers = []
    for arg in args.servers:
        for item in arg.replace(",", " ").split():
            if item.strip():
                raw_servers.append(item.strip())

    entries = []
    for i, item in enumerate(raw_servers, start=1):
        try:
            ip, port = item.rsplit(":", 1)
            entries.append(ServerEntry(id=f"srv-{i}", ip=ip, port=int(port)))
        except ValueError:
            sys.exit(f"Erro: Formato de servidor inválido '{item}' (esperado IP:PORTA)")

    if not entries:
        sys.exit("Erro: Nenhum servidor válido informado na opção --servers.")

    table = ServerTable(entries)
    monitor = HealthMonitor(
        server_table=table,
        interval=args.interval,
        timeout=args.timeout,
        auto_recover=args.auto_recover,
    )

    print(f"Configurados {len(entries)} servidores para monitoramento. Pressione Ctrl+C para encerrar.", flush=True)
    try:
        monitor.start()
        # Imprime a tabela imediatamente para visualização instantânea
        print(table.format_table() + "\n", flush=True)
        while True:
            time.sleep(5)
            print(table.format_table() + "\n", flush=True)
    except KeyboardInterrupt:
        print("\nEncerrando monitor...", flush=True)
    finally:
        monitor.stop()
