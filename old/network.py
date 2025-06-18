import socket, time, re, random

class Network:
    def __init__(self):
        self.client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server = '10.1.148.22'
        self.port = 5555
        self.addr = (self.server, self.port)
        self.event_outcome = '2' # or '2'
        self.probability = 0.25
        self.connect()

    def connect(self):
        try:
            self.client.connect(self.addr)
            print("Connected to server")
            self.listen_for_updates()
        except Exception as e:
            print(f"Connection error: {e}")
            exit()

    def linear_score(self, turn_number, p1_start=2, p2_start=1, increment=2): # starts at 0
        # turn_number -= 1
        if turn_number == 0:
            player1 = p1_start
            player2 = p2_start
        else:
            player1 = p1_start + (turn_number // 2) * increment
            player2 = p2_start + ((turn_number+1) // 2) * increment
        return player1, player2
    
    def strip_game_log(self, game_log):
        ids, nodes = game_log.split('_')
        length = len(nodes)
        count = 0
        for i in nodes:
            if i == '1':
                count += 1
            elif i == '2':
                count += 2
        turns = length - count
        return turns

    def listen_for_updates(self):
        game_log = 'xxx'
        while True:
            try:
                response = self.client.recv(2048).decode()
                if response[-1] == 'x':
                    print("Game over")
                    # print('') # put end amount here
                    game_log = 'xxx'
                elif 'bypass' in response:
                    print("Bypass detected")
                elif 'done' in response:
                    print("Game finished")
                    # print('') # put end amount here
                    game_log = 'xxx'
                
                else:
                    if len(game_log) <= 5:
                        print("New Game")
                    game_log = response
                    turn_number = self.strip_game_log(game_log)
                    print(f'current score?{self.linear_score(turn_number, p1_start=2, p2_start=1, increment=2)}')

                    move = input("Enter your move (pass/take): ").strip().lower()
                    while move not in ['pass', 'take']:
                        print("Invalid input. Choose 'pass' or 'take'.")
                        move = input("Enter your move (pass/take): ").strip().lower()

                    if move == "take":
                        self.client.send(str.encode(f'{game_log}_x'))
                        game_log = 'xxx'
                    else:
                        if random.random() < self.probability:
                            game_log = game_log + self.event_outcome
                        else:
                            game_log = game_log + '0'
                        
                        self.client.send(str.encode(game_log))

            except Exception as e:
                print(f"Connection lost: {e}")
                break

n = Network()