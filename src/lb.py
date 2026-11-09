import argparse 
from server_table import ServerEntry

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
                   metavar="IP:PORTA",
                   help="Lista de backends no formato IP:PORTA para o lb escutar")

    return parser.parse_args()

def parse_servers(items):
    '''Retorna os servidores criados com base nos argumentos passados
    items: Valores recebidos de parser_args

    Retorna uma lista de serverEntries
    '''

    out = []
    for i, s in enumerate(items, start=1):
        try:
            ip, port = s.rsplit(":", 1)
            out.append(ServerEntry(id=f"srv-{i}", ip=ip, port=int(port)))
        except ValueError:
            raise SystemExit(f"Formato inválido: {s!r} (esperado IP:PORTA)")
    return out

if __name__ == "__main__":
    args = parse_args()
    print(args.port, args.policy)
