#!/bin/bash
# ==============================================================================
# Script para recuperar / reiniciar um servidor backend após uma falha
# Uso: ./scripts/recover_server.sh <ID> <PORTA> [HOST]
# Exemplo: ./scripts/recover_server.sh srv-1 8001
# ==============================================================================

ID="$1"
PORT="$2"
HOST="${3:-127.0.0.1}"

if [ -z "$ID" ] || [ -z "$PORT" ]; then
    echo "Uso: $0 <ID> <PORTA> [HOST]"
    echo "Exemplo:"
    echo "  $0 srv-1 8001"
    exit 1
fi

# Verifica se já há algum processo rodando nessa porta
EXISTING_PID=$(lsof -ti :"$PORT" 2>/dev/null || true)
if [ -n "$EXISTING_PID" ]; then
    echo "[recover_server] Aviso: Já existe um processo escutando na porta $PORT (PID $EXISTING_PID)."
    exit 1
fi

echo "[recover_server] Reiniciando servidor '$ID' em $HOST:$PORT em segundo plano..."
python3 src/server.py --host "$HOST" --port "$PORT" --id "$ID" >/dev/null 2>&1 &
NEW_PID=$!

sleep 0.5
if kill -0 "$NEW_PID" 2>/dev/null; then
    echo "[recover_server] Servidor '$ID' iniciado com sucesso (PID $NEW_PID)!"
else
    echo "[recover_server] Erro: Falha ao iniciar servidor '$ID'."
    exit 1
fi
