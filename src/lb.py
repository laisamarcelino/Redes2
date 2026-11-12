import argparse 
import socket
import threading

try:
    from server_table import ServerTable, ServerEntry, Status
    from monitor import HealthMonitor
except ImportError:
    from src.server_table import ServerTable, ServerEntry, Status
    from src.monitor import HealthMonitor

def _parse_ip_port(spec: str) -> tuple[str, int]:
    '''
    Validando formato de ip-porta que o usuário passar
    '''
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
    '''
    Definindo os argumentos que lb.py aceita
    
    port -> A porta em que LB escuta
    policy -> A política de decisão do LB
    servers -> Lista de servidores IP:PORTA para o LB controlar

    Retorna um parser com os argumentos aceitos
    '''

    parser = argparse.ArgumentParser(
            prog="Load Balancer TCP",
            description="Defina um Load Balancer para gerenciar recursos de servidores!",
            epilog="placeholder")

    parser.add_argument("--port", type=int, required=True,
                   help="Porta de escuta do LB")

    parser.add_argument("--policy", choices=["round-robin", "least-connections"],
                   required=True, help="Política de balanceamento")

    parser.add_argument("--servers", nargs="+", required=True,
                   metavar="IP:PORTA", type=_parse_ip_port,
                   help="Lista de backends no formato IP:PORTA para o lb escutar")

    return parser.parse_args()

def parse_servers(specs: list[tuple[str, int]]) -> list[ServerEntry]:
    """Gera ServerEntry a partir dos pares (ip, port) já validados pelo argparse."""
    return [
        ServerEntry(id=f"srv-{i}", ip=ip, port=port)
        for i, (ip, port) in enumerate(specs, start=1)
    ]

def relay(src, dst):
    '''
    Encaminhador de bytes do LB.
    Ela copia dados de um socket para outro, numa direção, até a conexão terminar.
    Permite que o LB vire de fato um proxy, intermediário.
    '''

    try:
        while True:
            data = src.recv(4096)
            print(f"[relay] lido {data!r} de {src.getpeername()}", flush=True)
            if not data:
                break
            dst.sendall(data)
            print(f"[relay] enviado {data!r} para {dst.getpeername()}", flush=True)
    except OSError as e:
        print(f"[relay] OSError: {e!r}", flush=True)
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass

def handle_client(conn, addr, table, policy):
    # ESCOLHA + RESERVA ATÔMICAS
    backend = table.select_and_reserve(policy)
    if backend is None:
        print(f"[LB] nenhum backend UP; rejeitando {addr}", flush=True)
        conn.close()
        return

    # conecta ao backend escolhido
    try:
        upstream = socket.create_connection((backend.ip, backend.port), timeout=2)
        upstream.settimeout(None)
    except OSError:
        table.decrement_connection(backend.id)   # devolve a reserva
        conn.close()
        return

    try:
        print(f"[LB] {addr} -> {backend.id} ({backend.ip}:{backend.port})", flush=True)
        # relay bidirecional
        t = threading.Thread(target=relay, args=(upstream, conn), daemon=True)
        t.start()
        relay(conn, upstream)
        t.join()
    finally:
        conn.close()
        upstream.close()
        table.decrement_connection(backend.id)   # devolve SEMPRE

def serve(port, table, policy):
    '''
    Abre a porta do LB e espera conexões de clientes
    Caso haja conexão de cliente, passa para handler que alocará um servidor
    '''
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(128)
    print(f"[LB] escutando em 0.0.0.0:{port} (policy={policy})", flush=True)
    try:
        while True:
            conn, addr = srv.accept()
            threading.Thread(
                target=handle_client,
                args=(conn, addr, table, policy),   # ← table e policy passados
                daemon=True,
            ).start()
    finally:
        srv.close()


if __name__ == "__main__":
    args = parse_args()
    entries = parse_servers(args.servers)
    table = ServerTable(entries)

    print(f"[LB] {len(entries)} backends; policy={args.policy}; porta={args.port}")

    print(table.format_table())
    monitor = HealthMonitor(table, interval=2.0, timeout=1.0)
    monitor.start()

    try:
        print(table.format_table())
        serve(args.port, table, args.policy)
    finally:
        monitor.stop()
    serve(args.port, table, args.policy)
