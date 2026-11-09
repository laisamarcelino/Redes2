import argparse 
import socket 
import threading 

MAX_SIZE = 1024

def parse_args(argv=None):
    '''
    Definindo os argumentos que server.py aceita
    
    host -> ip do servidor 
    port -> porta do servidor 
    id -> nome lógico do servidor

    Importante: cada instância de server.py representa um servidor,
    o lb.py recebe esses endereços via --servers

    Retorna um parser com os argumentos aceitos
    '''

    p = argparse.ArgumentParser(description="Servidor backend TCP")

    p.add_argument("--host", default="127.0.0.1")

    p.add_argument("--port", type=int, required=True)
    p.add_argument("--id", required=True)
    return p.parse_args(argv)

def handle_client(conn: socket.socket, addr: tuple, server_id: str):
    '''
    Cada cliente aceito gera um conn diferente. 
    É por isso que cada thread recebe o seu: threads diferentes não compartilham conn, 
    então não há corrida no socket (o kernel serializa operações no mesmo FD, mas o fluxo lógico é de um cliente só).

    conn: Socket daquela conexão
    addr: Tupla (IP, PORTA) da conexão, para log 
    server_id: Nome do servidor que está recebendo a conexão 
    '''
    
    with conn:
        try:
            while True:
                buf = b""
                while b"\n" not in buf: #TCP é stream, a mensagem pode não chegar completa, acumular
                    data = conn.recv(MAX_SIZE)
                    if not data:
                        break
                    
                    buf += data

                line, _, buf = buf.partition(b"\n")

                conn.sendall(b"OK\n")

        except (ConnectionResetError, OSError):
            pass

        # talvez logar aqui (pra isso que addr e server_id estao como argumentos) 

def run_server(host: str, port: int, server_id: str):
    '''
    Roda de fato o servidor com handle_client em uma thread nova
    
    host: Endereço IP do servidor
    port: Porta do servidor 
    server_id: Nome do servidor
    '''

    #TALVEZ RODAR POR UM TEMPO LIMITADO
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # evita "Address already in use" no restart
    srv.bind((host, port))
    srv.listen(128)

    print(f"[{server_id}] escutando em {host}:{port}")

    try:
        while True:
            conn, addr = srv.accept()
            t = threading.Thread(
                target=handle_client, #Rodando a função handle_client
                args=(conn, addr, server_id),
                daemon=True, # não impede o processo de sair
            )

            t.start()
    finally:
        srv.close()

if __name__ == "__main__":
    args = parse_args()
    run_server(args.host, args.port, args.id)
