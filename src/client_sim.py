import argparse
import random
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import List


@dataclass
class ClientResult:
    client_id: int
    duration_target: float
    duration_actual: float = 0.0
    connected: bool = False
    requests_sent: int = 0
    responses_ok: int = 0
    errors: int = 0
    latencies_ms: List[float] = field(default_factory=list)
    error_message: str = ""


def parse_args():
    parser = argparse.ArgumentParser(
        prog="client_sim.py",
        description="Simulador de Carga Concorrente de Clientes TCP para o Load Balancer",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Endereço IP do Load Balancer (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        required=True,
        help="Porta TCP do Load Balancer",
    )
    parser.add_argument(
        "-m",
        "--clients",
        type=int,
        default=10,
        help="Número total M de clientes concorrentes (default: 10)",
    )
    parser.add_argument(
        "-k",
        "--duration",
        type=float,
        default=5.0,
        help="Duração K da conexão em segundos para cada cliente (default: 5.0)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.5,
        help="Intervalo entre o envio de requisições por cliente em segundos (default: 0.5)",
    )
    parser.add_argument(
        "--random-duration",
        action="store_true",
        help="Habilita duração heterogênea para cada cliente (ideal para avaliar a política Least Connections)",
    )
    parser.add_argument(
        "--min-duration",
        type=float,
        default=None,
        help="Duração mínima quando --random-duration estiver ativo (default: 0.5 * duration)",
    )
    parser.add_argument(
        "--max-duration",
        type=float,
        default=None,
        help="Duração máxima quando --random-duration estiver ativo (default: 1.5 * duration)",
    )
    parser.add_argument(
        "--ramp-up",
        type=float,
        default=0.0,
        help="Intervalo em segundos para espalhar o início dos clientes (default: 0.0 - disparo simultâneo)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=3.0,
        help="Timeout de socket TCP em segundos (default: 3.0)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Exibe logs detalhados de cada requisição individual",
    )

    return parser.parse_args()


def simulate_client(
    client_id: int,
    host: str,
    port: int,
    target_duration: float,
    interval: float,
    timeout: float,
    start_barrier: threading.Barrier,
    result: ClientResult,
    verbose: bool,
) -> None:
    """Thread trabalhadora que simula o ciclo de vida completo de um cliente."""
    result.duration_target = target_duration

    # Sincroniza o início de todos os clientes para gerar pico de carga concorrente
    if start_barrier:
        try:
            start_barrier.wait()
        except threading.BrokenBarrierError:
            pass

    t_start = time.time()
    sock = None

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        result.connected = True
        if verbose:
            print(f"[Cliente #{client_id:02d}] Conectado ao LB em {host}:{port}")

        buf = b""
        seq = 0

        while (time.time() - t_start) < target_duration:
            seq += 1
            payload = f"CLIENT-{client_id} REQ-{seq}\n".encode()

            req_t0 = time.perf_counter()
            try:
                sock.sendall(payload)
                result.requests_sent += 1

                # Lê a resposta até encontrar '\n'
                while b"\n" not in buf:
                    chunk = sock.recv(1024)
                    if not chunk:
                        raise ConnectionResetError("Conexão fechada prematuramente pelo servidor.")
                    buf += chunk

                line, _, buf = buf.partition(b"\n")
                req_t1 = time.perf_counter()
                latency_ms = (req_t1 - req_t0) * 1000.0

                if line.strip() == b"OK":
                    result.responses_ok += 1
                    result.latencies_ms.append(latency_ms)
                    if verbose:
                        print(
                            f"[Cliente #{client_id:02d}] REQ #{seq} -> OK ({latency_ms:.2f} ms)"
                        )
                else:
                    result.errors += 1
                    if verbose:
                        print(
                            f"[Cliente #{client_id:02d}] REQ #{seq} -> Resposta inesperada: {line!r}"
                        )

            except (socket.timeout, ConnectionResetError, BrokenPipeError, OSError) as e:
                result.errors += 1
                result.error_message = str(e)
                if verbose:
                    print(f"[Cliente #{client_id:02d}] Erro durante envio/recepção: {e}")
                break

            # Respeita o intervalo configurado entre envios
            time.sleep(interval)

    except (ConnectionRefusedError, socket.timeout, OSError) as e:
        result.errors += 1
        result.error_message = f"Falha na conexão inicial: {e}"
        if verbose:
            print(f"[Cliente #{client_id:02d}] Não foi possível conectar ao LB: {e}")
    finally:
        result.duration_actual = time.time() - t_start
        if sock:
            try:
                sock.close()
            except OSError:
                pass
        if verbose and result.connected:
            print(
                f"[Cliente #{client_id:02d}] Desconectado após {result.duration_actual:.2f}s "
                f"(OK: {result.responses_ok}, Erros: {result.errors})"
            )


def print_summary(
    results: List[ClientResult],
    total_time: float,
    host: str,
    port: int,
    m_clients: int,
    k_duration: float,
) -> None:
    """Imprime relatório estruturado com métricas globais e distribuição de resultados."""
    total_connected = sum(1 for r in results if r.connected)
    total_requests = sum(r.requests_sent for r in results)
    total_ok = sum(r.responses_ok for r in results)
    total_errors = sum(r.errors for r in results)

    all_latencies = [lat for r in results for lat in r.latencies_ms]
    avg_latency = (sum(all_latencies) / len(all_latencies)) if all_latencies else 0.0
    min_latency = min(all_latencies) if all_latencies else 0.0
    max_latency = max(all_latencies) if all_latencies else 0.0

    success_rate = (total_ok / total_requests * 100.0) if total_requests > 0 else 0.0
    throughput = (total_ok / total_time) if total_time > 0 else 0.0

    print("\n" + "=" * 70)
    print("           RELATÓRIO DE EXECUÇÃO DO GERADOR DE CARGA           ")
    print("=" * 70)
    print(f"Alvo (Load Balancer)        : {host}:{port}")
    print(f"Clientes Concorrentes (M)   : {m_clients}")
    print(f"Duração Planejada (K)       : {k_duration:.1f} s")
    print(f"Tempo Total de Teste        : {total_time:.2f} s")
    print("-" * 70)
    print(f"Conexões Estabelecidas      : {total_connected} / {m_clients} ({(total_connected/m_clients*100.0 if m_clients else 0):.1f}%)")
    print(f"Requisições Enviadas        : {total_requests}")
    print(f"Respostas 'OK' Recebidas    : {total_ok}")
    print(f"Falhas / Erros de Conexão   : {total_errors}")
    print(f"Taxa de Sucesso Global      : {success_rate:.2f}%")
    print(f"Throughput Médio            : {throughput:.2f} req/s")
    print("-" * 70)
    print(f"Latência Média              : {avg_latency:.2f} ms")
    print(f"Latência Mínima             : {min_latency:.2f} ms")
    print(f"Latência Máxima             : {max_latency:.2f} ms")
    print("=" * 70)

    # Detalhamento por cliente
    print("\nDetalhamento por Cliente:")
    print("+---------+-----------+-----------+----------+----------+----------+----------------+")
    print("| Cliente | Duração(s)| Conectado | Enviadas | OK Recv  | Erros    | Latência Média |")
    print("+---------+-----------+-----------+----------+----------+----------+----------------+")
    for r in results:
        status_conn = "SIM" if r.connected else "NÃO"
        client_avg = (
            f"{sum(r.latencies_ms)/len(r.latencies_ms):.2f} ms"
            if r.latencies_ms
            else "N/A"
        )
        print(
            f"| #{r.client_id:<6d} | {r.duration_actual:<9.2f} | {status_conn:<9} | {r.requests_sent:<8d} | {r.responses_ok:<8d} | {r.errors:<8d} | {client_avg:<14} |"
        )
    print("+---------+-----------+-----------+----------+----------+----------+----------------+\n")


def main():
    args = parse_args()

    # Cálculo da duração de cada cliente (fixa ou com dispersão para Least Connections)
    min_dur = args.min_duration if args.min_duration is not None else max(1.0, args.duration * 0.5)
    max_dur = args.max_duration if args.max_duration is not None else args.duration * 1.5

    durations: List[float] = []
    for _ in range(args.clients):
        if args.random_duration:
            durations.append(random.uniform(min_dur, max_dur))
        else:
            durations.append(args.duration)

    print(
        f"[SIMULADOR] Iniciando {args.clients} clientes concorrentes contra o LB {args.host}:{args.port}..."
    )
    if args.random_duration:
        print(
            f"[SIMULADOR] Modo de duração heterogênea ativado (faixa: {min_dur:.1f}s a {max_dur:.1f}s)"
        )
    else:
        print(f"[SIMULADOR] Duração uniforme por cliente: {args.duration:.1f}s")

    # Barreira para sincronizar o disparo simultâneo dos clientes caso ramp_up seja zero
    barrier = (
        threading.Barrier(args.clients + 1)
        if args.ramp_up <= 0
        else None
    )

    results: List[ClientResult] = [ClientResult(client_id=i + 1, duration_target=durations[i]) for i in range(args.clients)]
    threads: List[threading.Thread] = []

    for i in range(args.clients):
        t = threading.Thread(
            target=simulate_client,
            args=(
                i + 1,
                args.host,
                args.port,
                durations[i],
                args.interval,
                args.timeout,
                barrier,
                results[i],
                args.verbose,
            ),
            name=f"ClientSim-{i+1}",
        )
        threads.append(t)
        t.start()
        if args.ramp_up > 0:
            time.sleep(args.ramp_up / args.clients)

    t_global_start = time.time()
    if barrier:
        # Libera a barreira para todos os clientes começarem simultaneamente
        barrier.wait()

    # Aguarda o término de todos os clientes
    for t in threads:
        t.join()

    t_global_total = time.time() - t_global_start

    print_summary(
        results=results,
        total_time=t_global_total,
        host=args.host,
        port=args.port,
        m_clients=args.clients,
        k_duration=args.duration,
    )

    total_ok = sum(r.responses_ok for r in results)
    if total_ok == 0 and args.clients > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
