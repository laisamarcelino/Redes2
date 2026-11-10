import argparse 
import socket 
import threading 
import signal
import os
import sys

MAX_SIZE = 1024

def parse_args(argv=None):
    '''
    Definindo os argumentos que server.py aceita
    
    host -> ip do servidor 
    port -> porta do servidor 
    id -> nome lógico do servidor
    pid-file -> caminho opcional para gravar o PID do processo (facilita scripts de falha/recuperação)

    Importante: cada instância de server.py representa um servidor,
    o lb.py recebe esses endereços via --servers

    Retorna um parser com os argumentos aceitos
    '''

    p = argparse.ArgumentParser(description="Servidor backend TCP")

    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--id", required=True)
    p.add_argument("--pid-file", default=None,
                   help="Caminho opcional para salvar o PID deste servidor")
    return p.parse_args(argv)

def handle_client(conn: socket.socket, addr: tuple, server_id: str):
    '''
    conn: Socket daquela conexão
    addr: Tupla (IP, PORTA) da conexão, para log 
    server_id: Nome do servidor que está recebendo a conexão 
    '''
    
    with conn:
        try:
            buf = b""
            while not shutdown.is_set():
                data = conn.recv(MAX_SIZE)
                if not data:
                    break
                buf += data

                while b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    conn.sendall(b"OK\n")

        except (ConnectionResetError, BrokenPipeError, OSError):
            pass

shutdown = threading.Event()

def on_signal(signum, frame):
    '''
    Handler de recebimento de sinal para encerramento (SIGINT, SIGTERM)
    '''
    print(f"[{os.getpid()}] sinal {signum} recebido, iniciando shutdown")
    shutdown.set()

def on_crash_signal(signum, frame):
    '''
    Handler de simulação de pane instantânea / crash forçado (SIGUSR1)
    Simula uma falha abrupta de hardware ou kill -9 sem cleanup
    '''
    print(f"[{os.getpid()}] SINAL DE PANE/FALHA ABRUPTA ({signum}) RECEBIDO! Encerrando imediatamente.")
    os._exit(1)

def run_server(host: str, port: int, server_id: str, pid_file: str = None):
    '''
    Roda de fato o servidor com handle_client em uma thread nova
    
    host: Endereço IP do servidor
    port: Porta do servidor 
    server_id: Nome do servidor
    pid_file: Arquivo opcional para salvar o PID
    '''

    # Grava o PID no arquivo se especificado
    if pid_file:
        try:
            with open(pid_file, "w") as f:
                f.write(str(os.getpid()))
        except OSError as e:
            print(f"[{server_id}] Aviso: não foi possível gravar pid-file {pid_file}: {e}")

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # evita "Address already in use" no restart
    srv.bind((host, port))
    srv.listen(128)
    srv.settimeout(0.5)

    print(f"[{server_id}] escutando em {host}:{port} (PID: {os.getpid()})")

    try:
        while not shutdown.is_set():
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue

            t = threading.Thread(
                target=handle_client,
                args=(conn, addr, server_id),
                daemon=True,
            )
            t.start()
    finally:
        srv.close()
        if pid_file and os.path.exists(pid_file):
            try:
                os.remove(pid_file)
            except OSError:
                pass
        print(f"[{server_id}] encerrado")

if __name__ == "__main__":
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    if hasattr(signal, "SIGUSR1"):
        signal.signal(signal.SIGUSR1, on_crash_signal)

    args = parse_args()
    run_server(args.host, args.port, args.id, args.pid_file)
