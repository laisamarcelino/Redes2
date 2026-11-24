# Testes dos cenários C1 a C4. Uso: bash teste/run_tests.sh
#
# Os 3 servidores sobem uma vez só. Os cenários rodam em sequência:
# C1 e C2 testam as políticas, C3 derruba o srv-2 e C4 reinicia o srv-2.

cd "$(dirname "$0")/.."
LB=${PORTA:-8000} 
S1=$((LB + 1)); S2=$((LB + 2)); S3=$((LB + 3))
SERVIDORES="127.0.0.1:$S1 127.0.0.1:$S2 127.0.0.1:$S3"
ERROS=0
trap 'kill $(jobs -p) 2>/dev/null' EXIT      # no final, desliga o que ficou rodando

# confere "descrição" obtido esperado  -> mostra PASS ou FAIL
confere() {
    if [ "$2" = "$3" ]; then
        echo "  PASS  $1 ($2)"
    else
        echo "  FAIL  $1 (obtido=$2, esperado=$3)"
        ERROS=$((ERROS + 1))
    fi
}

echo "Subindo os 3 servidores..."
python3 src/server.py --port $S1 --id srv-1 > /dev/null 2>&1 &
P1=$!
python3 src/server.py --port $S2 --id srv-2 > /dev/null 2>&1 &
P2=$!
python3 src/server.py --port $S3 --id srv-3 > /dev/null 2>&1 &
P3=$!
sleep 1

# ------------------------------------------------------------------
echo
echo "### C1: Round-Robin, 30 clientes -> 10 por servidor"
python3 -u src/lb.py --port $LB --policy round-robin --servers $SERVIDORES > /tmp/lb_c1.log 2>&1 &
LBPID=$!
sleep 3

python3 src/client_sim.py --port $LB -m 30 -k 3 > /tmp/cli_c1.log
sleep 1
kill $LBPID; wait $LBPID                     # o LB imprime a tabela final ao parar

confere "srv-1 recebeu 10 conexões" "$(grep -c 'ROTEAMENTO.*-> srv-1 ' /tmp/lb_c1.log)" 10
confere "srv-2 recebeu 10 conexões" "$(grep -c 'ROTEAMENTO.*-> srv-2 ' /tmp/lb_c1.log)" 10
confere "srv-3 recebeu 10 conexões" "$(grep -c 'ROTEAMENTO.*-> srv-3 ' /tmp/lb_c1.log)" 10
confere "conexões ativas no fim" "$(grep -o 'Ativas agora: [0-9]*' /tmp/lb_c1.log | tail -1)" "Ativas agora: 0"

# ------------------------------------------------------------------
echo
echo "### C2: Least Connections. 2 clientes longos ocupam srv-1 e srv-2; os 6 curtos vão para o srv-3"
python3 -u src/lb.py --port $LB --policy least-connections --servers $SERVIDORES > /tmp/lb_c2.log 2>&1 &
LBPID=$!
sleep 3

python3 src/client_sim.py --port $LB -m 2 -k 12 > /dev/null &      # clientes longos, em segundo plano
LONGOS=$!
sleep 1.5
python3 src/client_sim.py --port $LB -m 6 -k 1 --interval 0.25 --ramp-up 9 > /tmp/cli_c2.log   # curtos, um de cada vez
wait $LONGOS                                                        # espera os longos terminarem
sleep 1
kill $LBPID; wait $LBPID

confere "srv-1 (cliente longo) recebeu 1 conexão" "$(grep -c 'ROTEAMENTO.*-> srv-1 ' /tmp/lb_c2.log)" 1
confere "srv-2 (cliente longo) recebeu 1 conexão" "$(grep -c 'ROTEAMENTO.*-> srv-2 ' /tmp/lb_c2.log)" 1
confere "srv-3 (livre) recebeu os 6 curtos" "$(grep -c 'ROTEAMENTO.*-> srv-3 ' /tmp/lb_c2.log)" 6
confere "conexões ativas no fim" "$(grep -o 'Ativas agora: [0-9]*' /tmp/lb_c2.log | tail -1)" "Ativas agora: 0"

# ------------------------------------------------------------------
echo
echo "### C3: derruba o srv-2 com carga rodando (Round-Robin)"
python3 -u src/lb.py --port $LB --policy round-robin --servers $SERVIDORES > /tmp/lb_c3.log 2>&1 &
LBPID=$!
sleep 3

python3 src/client_sim.py --port $LB -m 12 -k 8 > /tmp/cli_c3a.log &   # lote 1, em segundo plano
LOTE1=$!
sleep 2
kill -USR1 $P2                                # derruba o srv-2 de repente
sleep 4                                       # tempo para o monitor perceber

confere "monitor detectou a queda do srv-2" "$(grep -c 'FALHA DETECTADA.*srv-2' /tmp/lb_c3.log)" 1

# guarda quantas conexões cada servidor já tinha, antes do lote 2
ANTES1=$(grep -c 'ROTEAMENTO.*-> srv-1 ' /tmp/lb_c3.log)
ANTES2=$(grep -c 'ROTEAMENTO.*-> srv-2 ' /tmp/lb_c3.log)
ANTES3=$(grep -c 'ROTEAMENTO.*-> srv-3 ' /tmp/lb_c3.log)

python3 src/client_sim.py --port $LB -m 12 -k 2 > /tmp/cli_c3b.log      # lote 2
wait $LOTE1
sleep 1
kill $LBPID; wait $LBPID

DEPOIS1=$(grep -c 'ROTEAMENTO.*-> srv-1 ' /tmp/lb_c3.log)
DEPOIS2=$(grep -c 'ROTEAMENTO.*-> srv-2 ' /tmp/lb_c3.log)
DEPOIS3=$(grep -c 'ROTEAMENTO.*-> srv-3 ' /tmp/lb_c3.log)

confere "lote 1: 4 clientes do srv-2 tiveram erro" "$(grep Falhas /tmp/cli_c3a.log | head -1 | awk -F: '{ print $2 + 0 }')" 4
confere "lote 2: nenhum erro" "$(grep Falhas /tmp/cli_c3b.log | head -1 | awk -F: '{ print $2 + 0 }')" 0
confere "srv-2 não recebeu conexão nova" $DEPOIS2 $ANTES2
confere "srv-1 recebeu 6 do lote 2" $((DEPOIS1 - ANTES1)) 6
confere "srv-3 recebeu 6 do lote 2" $((DEPOIS3 - ANTES3)) 6
confere "conexões ativas no fim" "$(grep -o 'Ativas agora: [0-9]*' /tmp/lb_c3.log | tail -1)" "Ativas agora: 0"

# ------------------------------------------------------------------
echo
echo "### C4: o srv-2 continua caído; reinicia ele na mesma porta"
python3 -u src/lb.py --port $LB --policy round-robin --servers $SERVIDORES > /tmp/lb_c4.log 2>&1 &
LBPID=$!
sleep 3

python3 src/server.py --port $S2 --id srv-2 > /dev/null 2>&1 &         # reinicia o srv-2
P2=$!
sleep 4                                       # tempo para o monitor perceber

confere "monitor detectou a recuperação do srv-2" "$(grep -c 'RECUPERA.*srv-2' /tmp/lb_c4.log)" 1

python3 src/client_sim.py --port $LB -m 12 -k 2 > /tmp/cli_c4.log
sleep 1
kill $LBPID; wait $LBPID

confere "srv-1 recebeu 4 conexões" "$(grep -c 'ROTEAMENTO.*-> srv-1 ' /tmp/lb_c4.log)" 4
confere "srv-2 recebeu 4 conexões" "$(grep -c 'ROTEAMENTO.*-> srv-2 ' /tmp/lb_c4.log)" 4
confere "srv-3 recebeu 4 conexões" "$(grep -c 'ROTEAMENTO.*-> srv-3 ' /tmp/lb_c4.log)" 4
confere "conexões ativas no fim" "$(grep -o 'Ativas agora: [0-9]*' /tmp/lb_c4.log | tail -1)" "Ativas agora: 0"

# ------------------------------------------------------------------
echo
if [ $ERROS = 0 ]; then
    echo "TUDO PASSOU"
else
    echo "$ERROS verificações falharam"
    exit 1
fi