import socket, threading, time
from PSM import generate_PSM

def log_game(game_log, filename="game_logs.txt"):
        with open(filename, "a") as f:
            f.write(game_log + "\n")

class Player:
    def __init__(self, conn, addr, id):
        self.conn = conn
        self.addr = addr
        self.id = id
        self.opponent = None
    
    def handle_client(self):
        print(f"Player {self.id} connected from {self.addr}")
        try:
            while True:
                if self.opponent is None or self.opponent == 'bypass':
                    time.sleep(0.1)
                    continue

                data = self.conn.recv(2048)
                if not data:
                    continue

                msg = data.decode()
                print(msg)

                if self.opponent:
                    self.opponent.conn.send(msg.encode())

                    if msg[-1] == 'x':
                        log_game(msg)
                        self.opponent.opponent = None
                        self.opponent = None

        except Exception as e:
            print(f"Error: {e}")
        finally:
            print(f"Player {self.id} disconnected")
            self.conn.close()

players = []
player_id_counter = 1
lock = threading.Lock()
player_count = int(input("player_count: "))

host = '10.1.148.22'
port = 5555
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind((host, port))
server.listen()
print(f"Server listening on {host}:{port}")

while True:
    conn, addr = server.accept()
    with lock:
        player = Player(conn, addr, player_id_counter)
        player_id_counter += 1
        players.append(player)

    threading.Thread(target = player.handle_client).start()
    
    if player_count == len(players):
        if len(players) % 2 != 0:
            players.insert(0, 0)
        pairings = generate_PSM(players)

        for round in pairings:
            for game in round:
                p1, p2 = game
                if 0 in (p1, p2):
                    if p1 == 0:
                        p2.conn.send(f"{p2.id:02}bypass".encode())
                    else:
                        p1.conn.send(f"{p1.id:02}bypass".encode())
                else:
                    p1.opponent = p2
                    p2.opponent = p1
                    p1.conn.send(f"{p1.id:02}{p2.id:02}_".encode())
            
            going = True
            while going:
                going = False
                for player in players:
                    if player != 0:
                        if player.opponent is not None:
                            going = True
                            break
        
        for player in players:
            if player != 0:
                player.conn.send("done".encode())
                player.conn.close()
