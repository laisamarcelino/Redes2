import argparse 
try:
    from server_table import ServerTable, ServerEntry, Status
except ImportError:
    from src.server_table import ServerTable, ServerEntry, Status

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

if __name__ == "__main__":
    args = parse_args()
    print(args.port, args.policy)
