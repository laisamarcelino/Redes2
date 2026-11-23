import argparse
import signal
import socket
import sys
import threading
import time

try:
    from server_table import ServerTable, ServerEntry, Status
    from monitor import HealthMonitor
except ImportError:
    from src.server_table import ServerTable, ServerEntry, Status
    from src.monitor import HealthMonitor


def log_event(event_type: str, message: str) -> None:
    """Imprime uma mensagem de log padronizada com timestamp e categoria."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [LB] [{event_type}] {message}\n", end="", flush=True)


def _parse_ip_port(spec: str) -> tuple[str, int]:
    """Valida formato de IP:PORTA informado pelo usuário."""
    ip, sep, port_s = spec.rpartition(":")
    if not sep or not ip:
        raise argparse.ArgumentTypeError(f"formato inválido {spec!r}; esperado IP:PORTA")
    try:
        port = int(port_s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"porta não numérica em {spec!r}")
    if not (1 <= port <= 65535):
        raise argparse.ArgumentTypeError(f"porta fora de faixa em {spec!r}: {port}")

    return ip, port


def parse_args():
    """Define e processa os argumentos de linha de comando para o Load Balancer."""
    parser = argparse.ArgumentParser(
        prog="Load Balancer TCP",
        description="Sistema de Balanceamento de Carga TCP Concorrente com Políticas e Monitoramento.",
    )

    parser.add_argument(
        "--port",
        type=int,
        required=True,
        help="Porta TCP na qual o Load Balancer escutará conexões de clientes",
    )
    parser.add_argument(
        "--policy",
        choices=["round-robin", "least-connections"],
        required=True,
        help="Política de balanceamento de carga a ser aplicada",
    )
    parser.add_argument(
        "--servers",
        nargs="+",
        required=True,
        metavar="IP:PORTA",
        type=_parse_ip_port,
        help="Lista de servidores backend no formato IP:PORTA",
    )

    return parser.parse_args()


def parse_servers(specs: list[tuple[str, int]]) -> list[ServerEntry]:
    """Gera instâncias de ServerEntry a partir dos pares (ip, port) validados."""
    return [
        ServerEntry(id=f"srv-{i}", ip=ip, port=port)
        for i, (ip, port) in enumerate(specs, start=1)
    ]


def relay(src: socket.socket, dst: socket.socket) -> None:
    """
    Copia continuamente bytes de um socket para outro em uma única direção até o término da conexão.
    Permite comunicação bidirecional full-duplex de maneira não bloqueante entre cliente e backend.
    """
    try:
        while True:
            data = src.recv(4096)
            if not data:
                break
            dst.sendall(data)
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def handle_client(
    conn: socket.socket,
    addr: tuple[str, int],
    table: ServerTable,
    policy: str,
) -> None:
    """
    Atende uma conexão de cliente: seleciona o backend segundo a política,
    estabelece o túnel TCP de proxy bidirecional e atualiza as métricas da tabela.
    """
    client_str = f"{addr[0]}:{addr[1]}"
    log_event("CONEXÃO", f"Nova conexão recebida de {client_str}")

    # Escolha e reserva atômica do backend para evitar condição de corrida
    backend = table.select_and_reserve(policy)
    if backend is None:
        log_event(
            "REJEITADA",
            f"Nenhum servidor UP disponível para atender {client_str}; conexão recusada.",
        )
        try:
            conn.close()
        except OSError:
            pass
        return

    # Conecta ao servidor backend escolhido
    try:
        upstream = socket.create_connection((backend.ip, backend.port), timeout=2.0)
        upstream.settimeout(None)
    except OSError as e:
        # Se falhou a conexão física com o backend, estorna a reserva e rejeita o cliente
        table.decrement_connection(backend.id)
        log_event(
            "ERRO_BACKEND",
            f"Falha ao conectar no backend {backend.id} ({backend.ip}:{backend.port}) para cliente {client_str}: {e}. Reserva cancelada.",
        )
        try:
            conn.close()
        except OSError:
            pass
        return

    log_event(
        "ROTEAMENTO",
        f"{client_str} -> {backend.id} ({backend.ip}:{backend.port}) via '{policy}' "
        f"[Ativas no servidor: {backend.active_connections} | Total global: {table.get_total_global()}]",
    )

    try:
        # Cria thread para o sentido Backend -> Cliente
        t_upstream = threading.Thread(
            target=relay,
            args=(upstream, conn),
            name=f"Relay-{backend.id}->Client-{client_str}",
            daemon=True,
        )
        t_upstream.start()

        # Executa na thread atual o sentido Cliente -> Backend
        relay(conn, upstream)
        t_upstream.join()
    finally:
        try:
            conn.close()
        except OSError:
            pass
        try:
            upstream.close()
        except OSError:
            pass

        # Garante a devolução da conexão ativa na ServerTable
        table.decrement_connection(backend.id)
        current = table.get_server(backend.id)
        remaining = current.active_connections if current else 0
        log_event(
            "FINALIZADA",
            f"Conexão com {client_str} (atendida por {backend.id}) encerrada. "
            f"[Ativas restantes no servidor: {remaining}]",
        )


def serve(
    port: int,
    table: ServerTable,
    policy: str,
    stop_event: threading.Event,
) -> None:
    """Abre a porta de escuta do LB e despacha cada cliente para uma thread trabalhadora."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(128)
    srv.settimeout(0.5)

    log_event(
        "PRONTO",
        f"Load Balancer escutando em 0.0.0.0:{port} com política '{policy}'",
    )

    try:
        while not stop_event.is_set():
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            t = threading.Thread(
                target=handle_client,
                args=(conn, addr, table, policy),
                daemon=True,
            )
            t.start()
    finally:
        srv.close()
        log_event("PARADO", f"Socket listener na porta {port} encerrado.")


if __name__ == "__main__":
    args = parse_args()
    entries = parse_servers(args.servers)
    table = ServerTable(entries)

    print(
        f"\n[LB] Inicializando Load Balancer | Porta: {args.port} | "
        f"Política: {args.policy} | Servidores: {len(entries)}"
    )
    print(table.format_table() + "\n")

    # Inicia o monitor de integridade com health checks periódicos
    monitor = HealthMonitor(table, interval=2.0, timeout=1.0)
    monitor.start()

    stop_event = threading.Event()

    def handle_signal(sig, frame):
        log_event("SHUTDOWN", f"Sinal {sig} recebido. Finalizando Load Balancer...")
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        # Pequena pausa para permitir que a checagem inicial do monitor registre os servidores UP
        time.sleep(0.3)
        serve(args.port, table, args.policy, stop_event)
    finally:
        monitor.stop()
        print("\n[LB] Resumo final da Tabela de Servidores:")
        print(table.format_table())
