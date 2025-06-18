from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room
import random, os, datetime, time

app = Flask(__name__)
socketio = SocketIO(app)

# Session log setup
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)
session_log_filename = f"session_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
session_log_path = os.path.join(log_dir, session_log_filename)

# Data structures
players = {}  # sid -> {'game_log': str, 'opponent': sid, 'turn': bool}
waiting_players = []
current_round = []

# --- Helpers ---

def linear_score(turn_number, p1_start=2, p2_start=1, increment=2):
    if turn_number == 0:
        return p1_start, p2_start
    return p1_start + (turn_number // 2) * increment, p2_start + ((turn_number + 1) // 2) * increment

def strip_game_log(game_log):
    try:
        _, moves = game_log.split(':')
        return len(moves.split('|')) - 1 if moves else 0
    except ValueError:
        return 0

def save_game_log(game_log, sid1, sid2, final_score):
    if ':' not in game_log:
        return
    game_id, moves = game_log.split(':')
    with open(session_log_path, 'a') as f:
        f.write("=== Game Start ===\n")
        f.write(f"Time: {datetime.datetime.now()}\n")
        f.write(f"Game ID: {game_id}\n")
        f.write(f"Players: {sid1} vs {sid2}\n")
        f.write("Moves: " + moves.replace('|', ' -> ') + "\n")
        f.write(f"Final Score: P1={final_score[0]}, P2={final_score[1]}\n")
        f.write("=== Game End ===\n\n")

def round_robin(players):
    players = list(players)
    if len(players) % 2 == 1:
        players.append(None)  # Bye round for odd player
    n = len(players)
    rounds = []
    for _ in range(n - 1):
        pairs = []
        for j in range(n // 2):
            p1 = players[j]
            p2 = players[n - 1 - j]
            if p1 is not None and p2 is not None:
                pairs.append((p1, p2))
        rounds.append(pairs)
        players.insert(1, players.pop())
    return rounds

# --- Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/commander')
def commander():
    return render_template('commander.html')

# --- SocketIO Events ---

@socketio.on('commander_start')
def commander_start():
    start_game()

@socketio.on('commander_join')
def commander_join():
    join_room('commander')
    socketio.emit('update_players', {'players': [p[:4] for p in waiting_players]}, room='commander')

@socketio.on('join')
def handle_join():
    sid = request.sid
    players[sid] = {'game_log': 'xxx', 'opponent': None, 'turn': False}
    waiting_players.append(sid)
    emit('message', {'msg': 'Waiting for commander to start...'}, room=sid)
    socketio.emit('update_players', {'players': [p[:4] for p in waiting_players]}, room='commander')

def start_game():
    global current_round
    random.shuffle(waiting_players)
    current_round = round_robin(waiting_players)
    for round_index, round_pairings in enumerate(current_round):
        print(f"Round {round_index + 1}: {round_pairings}")
        for p1, p2 in round_pairings:
            players[p1]['opponent'] = p2
            players[p2]['opponent'] = p1
            short_id = f"{p1[:2]}{p2[:2]}"
            game_log = f"{short_id}:"
            players[p1]['game_log'] = game_log
            players[p2]['game_log'] = game_log
            players[p1]['turn'] = True
            players[p2]['turn'] = False
            emit('start', {'opponent': p2[:4], 'game_log': game_log}, room=p1)
            emit('start', {'opponent': p1[:4], 'game_log': game_log}, room=p2)
            emit('message', {'msg': 'Your turn! Choose a move:'}, room=p1)
            emit('message', {'msg': 'Waiting for opponent...'}, room=p2)

@socketio.on('move')
def handle_move(data):
    sid = request.sid
    move = data['move']
    player_data = players.get(sid)
    if not player_data:
        emit('message', {'msg': 'Player not found.'}, room=sid)
        return
    if not player_data.get('turn'):
        emit('message', {'msg': 'Not your turn.'}, room=sid)
        return
    opponent_sid = player_data.get('opponent')
    if not opponent_sid or opponent_sid not in players:
        emit('message', {'msg': 'No opponent found.'}, room=sid)
        return

    game_log = player_data['game_log']
    move_symbol = 'x' if move == 'take' else ('2' if random.random() < 0.25 else '0')
    if ':' not in game_log:
        emit('message', {'msg': 'Invalid game log format.'}, room=sid)
        return

    base, moves = game_log.split(':')
    moves = moves + '|' + move_symbol if moves else move_symbol
    updated_log = f"{base}:{moves}"
    players[sid]['game_log'] = updated_log
    players[opponent_sid]['game_log'] = updated_log

    turn_number = strip_game_log(updated_log)
    score = linear_score(turn_number)

    emit('update', {'score': score, 'log': updated_log}, room=sid)
    emit('update', {'score': score, 'log': updated_log}, room=opponent_sid)

    if move_symbol == 'x':
        save_game_log(updated_log, sid, opponent_sid, score)
        players[sid]['turn'] = False
        players[opponent_sid]['turn'] = False
    else:
        # Toggle turns
        players[sid]['turn'] = False
        players[opponent_sid]['turn'] = True
        emit('message', {'msg': 'Waiting for opponent...'}, room=sid)
        emit('message', {'msg': 'Your turn! Choose a move:'}, room=opponent_sid)

# --- Run App ---
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5001)
