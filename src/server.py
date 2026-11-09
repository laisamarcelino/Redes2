import argparse 

# server.py
def parse_args(argv=None):
    '''
    Definindo os argumentos que lb.py aceita
    
    host -> ip do servidor 
    port -> porta do servidor 
    id -> nome lógico do servidor

    Importante: cada instância de server.py representa um servidor,
    or argumentos passado aqui devem ser iguais aos do lb.py

    Retorna um parser com os argumentos aceitos
    '''

    p = argparse.ArgumentParser(description="Servidor backend TCP")

    p.add_argument("--host", default="127.0.0.1")

    p.add_argument("--port", type=int, required=True)
    p.add_argument("--id", required=True)
    return p.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    print(args.host, args.id)
