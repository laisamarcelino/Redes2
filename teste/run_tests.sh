#!/bin/bash
# Testes dos cenários C1 a C4 do load balancer.
#   bash tests/run_tests.sh          -> roda C1 a C4
#   bash tests/run_tests.sh C1 C3    -> roda só alguns
# Só imprime no terminal; os arquivos temporários em /tmp são apagados no final.
# Código de saída 0 = tudo passou.

cd "$(dirname "$0")/.." || exit 2
PORTA=${PORTA:-$((10000 + UID % 2000 * 10))}   # LB usa PORTA, servidores usam PORTA+1, +2, +3
TMP=$(mktemp -d)    # saída do LB e dos clientes que o script lê (apagada ao final)
FALHAS=0
trap 'kill $(jobs -p) 2>/dev/null; rm -rf $TMP' EXIT

# confere "o que está sendo testado" obtido esperado
confere() {
    if [ "$2" = "$3" ]; then
        echo "  PASS  $1 ($2)"
    else
        echo "  FAIL  $1 (obtido=$2, esperado=$3)"
        FALHAS=$((FALHAS + 1))
    fi
}

# ate "descrição" tempo_ms limite_ms
ate() {
    if [ $2 -le $3 ]; then r=sim; else r=nao; fi
    confere "$1 ($2 ms)" $r sim
}

ms() {
    echo $(($(date +%s%N) / 1000000))
}

# espera "padrão" n   -> espera o padrão aparecer n vezes no log do LB (até 15 s)
espera() {
    for t in $(seq 150); do
        if [ $(grep -c "$1" $LOG) -ge $2 ]; then
            return 0
        fi
        sleep 0.1
    done
    return 1
}

# sobe os 3 servidores e o LB com a política $1
sobe() {
    LOG=$TMP/$CENARIO-lb.log
    for i in 1 2 3; do
        python3 -u src/server.py --port $((PORTA + i)) --id srv-$i > /dev/null 2>&1 &
        SERV[$i]=$!
    done
    sleep 1
    python3 -u src/lb.py --port $PORTA --policy $1 \
        --servers 127.0.0.1:$((PORTA + 1)) 127.0.0.1:$((PORTA + 2)) 127.0.0.1:$((PORTA + 3)) > $LOG 2>&1 &
    LB=$!
    if ! espera PRONTO 1; then
        echo "  FAIL  LB não subiu (porta ocupada? tente PORTA=20000 bash tests/run_tests.sh)"
        exit 1
    fi
}

# para o LB (ele imprime a tabela final) e os servidores
para() {
    kill $LB
    wait $LB
    kill ${SERV[@]} 2>/dev/null
    wait
}

falha() {
    echo "  FAIL  $1"
    FALHAS=$((FALHAS + 1))
    para
}

# clientes <nome_do_log> <argumentos do client_sim.py>
clientes() {
    arq=$TMP/$CENARIO-$1.log
    shift
    python3 -u src/client_sim.py --port $PORTA "$@" > $arq 2>&1
}

# valor <nome_do_log> <rótulo>  -> número do resumo do client_sim
valor() {
    grep "$2" $TMP/$CENARIO-$1.log | head -1 | awk -F: '{ print $2 + 0 }'
}

# rotas <n>  -> conexões roteadas para srv-n
rotas() {
    grep ROTEAMENTO $LOG | grep -c -- "-> srv-$1 "
}

# tabela <n> <coluna>  -> valor na tabela final do LB (4=status, 5=ativas, 6=total)
tabela() {
    grep "| srv-$1 " $LOG | tail -1 | awk -F'|' -v c=$2 '{ gsub(/ /, "", $c); print $c }'
}

sem_vazamento() {
    for i in 1 2 3; do
        confere "srv-$i sem conexões ativas no fim" "$(tabela $i 5)" 0
    done
}

# ---------------------------------------------------------------- cenários

# C1: Round-Robin, 30 clientes iguais -> 10 por servidor
c1() {
    sobe round-robin
    clientes clientes -m 30 -k 3
    confere "clientes terminaram com código 0" $? 0
    confere "erros nos clientes" "$(valor clientes Falhas)" 0
    sleep 1
    para
    for i in 1 2 3; do
        confere "srv-$i recebeu 10 conexões" "$(rotas $i)" 10
    done
    sem_vazamento
}

# C2: Least Connections. 2 clientes longos ocupam srv-1 e srv-2;
# os curtos chegam um de cada vez, então todos devem ir para o srv-3 (o único livre)
c2() {
    sobe least-connections
    clientes longos -m 2 -k 12 &
    LONGOS=$!
    sleep 1.5
    clientes curtos -m 6 -k 1 --interval 0.25 --ramp-up 9
    confere "clientes curtos terminaram com código 0" $? 0
    wait $LONGOS
    sleep 1
    para
    confere "srv-1 (cliente longo) recebeu só 1 conexão" "$(rotas 1)" 1
    confere "srv-2 (cliente longo) recebeu só 1 conexão" "$(rotas 2)" 1
    confere "srv-3 (livre) recebeu os 6 curtos" "$(rotas 3)" 6
    sem_vazamento
}

# C3: derruba o srv-2 com carga rodando
c3() {
    sobe round-robin
    clientes lote1 -m 12 -k 8 &
    LOTE1=$!
    sleep 2

    t0=$(ms)
    kill -USR1 ${SERV[2]}
    espera "FALHA DETECTADA.*srv-2" 1 || { falha "monitor não detectou a queda do srv-2"; return; }
    ate "detecção da falha em até 5 s" $(($(ms) - t0)) 5000

    # guarda o que cada servidor já tinha recebido, e só então manda o lote 2
    r1=$(rotas 1)
    r2=$(rotas 2)
    r3=$(rotas 3)
    clientes lote2 -m 12 -k 2
    confere "lote 2 terminou com código 0" $? 0
    wait $LOTE1
    sleep 1
    para

    confere "lote 1: erros dos 4 clientes que estavam no srv-2" "$(valor lote1 Falhas)" 4
    confere "lote 2 sem erros" "$(valor lote2 Falhas)" 0
    confere "srv-2 não recebeu conexão nova depois do DOWN" "$(rotas 2)" $r2
    confere "srv-1 recebeu 6 do lote 2" $(($(rotas 1) - r1)) 6
    confere "srv-3 recebeu 6 do lote 2" $(($(rotas 3) - r3)) 6
    confere "srv-2 está DOWN na tabela final" "$(tabela 2 4)" DOWN
    sem_vazamento
}

# C4: derruba o srv-2, reinicia na mesma porta e confere que volta a receber
c4() {
    sobe round-robin
    kill -USR1 ${SERV[2]}
    espera "FALHA DETECTADA.*srv-2" 1 || { falha "monitor não detectou a queda do srv-2"; return; }

    t0=$(ms)
    python3 -u src/server.py --port $((PORTA + 2)) --id srv-2 > /dev/null 2>&1 &
    SERV[2]=$!
    # são 2 linhas: a da partida do LB (todo servidor começa DOWN) e a desta recuperação
    espera "RECUPERA.*srv-2" 2 || { falha "monitor não detectou a recuperação do srv-2"; return; }
    ate "recuperação em até 6 s" $(($(ms) - t0)) 6000

    clientes lote -m 12 -k 2
    confere "lote terminou com código 0" $? 0
    sleep 1
    para
    for i in 1 2 3; do
        confere "srv-$i recebeu 4 conexões" "$(rotas $i)" 4
    done
    confere "srv-2 está UP na tabela final" "$(tabela 2 4)" UP
    sem_vazamento
}

# ---------------------------------------------------------------- principal

for CENARIO in ${@:-C1 C2 C3 C4}; do
    CENARIO=${CENARIO^^}
    echo
    echo "### $CENARIO"
    case $CENARIO in
        C1) c1 ;;
        C2) c2 ;;
        C3) c3 ;;
        C4) c4 ;;
        *)  echo "  cenário desconhecido"; FALHAS=$((FALHAS + 1)) ;;
    esac
done

echo
if [ $FALHAS = 0 ]; then
    echo "TUDO PASSOU"
else
    echo "$FALHAS verificações falharam"
    exit 1
fi