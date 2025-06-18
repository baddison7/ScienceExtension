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
players = {}  # sid -> {'game_log': str, 'opponent': sid, 'turn': bool, 'ready_for_next_game': bool}
waiting_players = []
current_round_index = -1 # Tracks the current round being played (-1 means not started)
all_rounds_pairings = [] # Stores all generated round-robin pairings
games_in_current_round = {} # game_id -> {'p1_sid': sid, 'p2_sid': sid, 'completed': bool}

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

def round_robin(players_list): # Renamed 'players' to 'players_list' to avoid confusion with the global dict
    players_copy = list(players_list)
    if len(players_copy) % 2 == 1:
        players_copy.append(None)  # Bye round for odd player
    n = len(players_copy)
    rounds = []
    for _ in range(n - 1):
        pairs = []
        for j in range(n // 2):
            p1 = players_copy[j]
            p2 = players_copy[n - 1 - j]
            pairs.append((p1, p2))
        rounds.append(pairs)
        players_copy.insert(1, players_copy.pop())
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
    # Only allow starting if no rounds are currently in progress or all rounds are finished
    if current_round_index == -1 or current_round_index >= len(all_rounds_pairings):
        start_game_tournament()
    else:
        socketio.emit('message', {'msg': 'A tournament is already in progress or has unfinished rounds.'}, room='commander', namespace='/')


@socketio.on('commander_join')
def commander_join():
    join_room('commander')
    socketio.emit('update_players', {'players': [p[:4] for p in waiting_players]}, room='commander', namespace='/')

@socketio.on('join')
def handle_join():
    sid = request.sid
    # Initialize 'ready_for_next_game' to False when a player joins
    players[sid] = {'game_log': '', 'opponent': None, 'turn': False, 'ready_for_next_game': False}
    if sid not in waiting_players: # Prevent duplicate entries if player refreshes
        waiting_players.append(sid)
    socketio.emit('message', {'msg': 'Waiting for commander to start...'}, room=sid, namespace='/')
    socketio.emit('update_players', {'players': [p[:4] for p in waiting_players]}, room='commander', namespace='/')


def start_game_tournament():
    global all_rounds_pairings, current_round_index, games_in_current_round

    if not waiting_players:
        socketio.emit('message', {'msg': 'No players to start a tournament!'}, room='commander', namespace='/')
        return

    random.shuffle(waiting_players) # Shuffle once at the beginning of the tournament
    all_rounds_pairings = round_robin(waiting_players)
    current_round_index = 0
    socketio.emit('message', {'msg': f'Tournament started with {len(all_rounds_pairings)} rounds.'}, room='commander', namespace='/')
    play_next_round()

def play_next_round():
    global current_round_index, games_in_current_round

    if current_round_index >= len(all_rounds_pairings):
        socketio.emit('message', {'msg': 'Tournament finished!'}, room='commander', namespace='/')
        socketio.emit('message', {'msg': 'All rounds complete! Thanks for playing.'}, broadcast=True, namespace='/')
        # Reset for a new tournament
        reset_tournament_state()
        return

    current_round_pairings = all_rounds_pairings[current_round_index]
    games_in_current_round = {} # Reset for the new round
    socketio.emit('message', {'msg': f'Starting Round {current_round_index + 1} with {len(current_round_pairings)} games.'}, room='commander', namespace='/')

    active_games_in_round = 0

    for p1, p2 in current_round_pairings:
        if p1 is None or p2 is None: # Handle bye player
            bye_player_sid = p1 if p1 is not None else p2
            if bye_player_sid and bye_player_sid in players:
                players[bye_player_sid]['opponent'] = None
                players[bye_player_sid]['turn'] = False
                players[bye_player_sid]['game_log'] = '' # Clear any previous game log
                players[bye_player_sid]['ready_for_next_game'] = True # Mark as ready for next round
                socketio.emit('message', {'msg': f'Round {current_round_index + 1}: You have a BYE this round! Waiting for the next round...'}, room=bye_player_sid, namespace='/')
                socketio.emit('bye_status', {'has_bye': True, 'round': current_round_index + 1}, room=bye_player_sid, namespace='/') # New event for bye status
                print(f"Player {bye_player_sid[:4]} has a BYE in Round {current_round_index + 1}.")
            continue # Skip to next pairing

        # Handle actual game pairings
        if p1 not in players or p2 not in players:
            # Handle cases where a player might have disconnected
            if p1 in players:
                socketio.emit('message', {'msg': 'Your opponent disconnected. Waiting for next round or tournament restart.'}, room=p1, namespace='/')
                players[p1]['ready_for_next_game'] = True # Mark as ready to be re-matched
            if p2 in players:
                socketio.emit('message', {'msg': 'Your opponent disconnected. Waiting for next round or tournament restart.'}, room=p2, namespace='/')
                players[p2]['ready_for_next_game'] = True # Mark as ready to be re-matched
            print(f"Skipping pairing {p1} vs {p2} due to disconnected player(s).")
            continue

        active_games_in_round += 1
        players[p1]['opponent'] = p2
        players[p2]['opponent'] = p1
        short_id = f"{p1[:2]}{p2[:2]}"
        game_log = f"{short_id}:"
        players[p1]['game_log'] = game_log
        players[p2]['game_log'] = game_log
        players[p1]['turn'] = True
        players[p2]['turn'] = False
        players[p1]['ready_for_next_game'] = False # Not ready until game is over
        players[p2]['ready_for_next_game'] = False # Not ready until game is over

        # Store game info for tracking completion
        games_in_current_round[short_id] = {'p1_sid': p1, 'p2_sid': p2, 'completed': False}

        socketio.emit('start', {'opponent': p2[:4], 'game_log': game_log, 'round': current_round_index + 1}, room=p1, namespace='/')
        socketio.emit('start', {'opponent': p1[:4], 'game_log': game_log, 'round': current_round_index + 1}, room=p2, namespace='/')
        socketio.emit('message', {'msg': 'Your turn! Choose a move:'}, room=p1, namespace='/')
        socketio.emit('message', {'msg': 'Waiting for opponent...'}, room=p2, namespace='/')

    if active_games_in_round == 0 and current_round_pairings: # If all pairs were byes or disconnected
        socketio.emit('message', {'msg': f'Round {current_round_index + 1} has no active games. Advancing to next round.'}, room='commander', namespace='/')
        current_round_index += 1
        socketio.sleep(1) # Small delay
        socketio.start_background_task(target=play_next_round)


def reset_tournament_state():
    global current_round_index, all_rounds_pairings, games_in_current_round, waiting_players
    current_round_index = -1
    all_rounds_pairings = []
    games_in_current_round = {}
    # Optionally clear waiting_players or mark all players as 'available'
    # For now, let's keep waiting_players as they are to allow re-joining
    for sid in players:
        players[sid] = {'game_log': '', 'opponent': None, 'turn': False, 'ready_for_next_game': False}
    socketio.emit('update_players', {'players': [p[:4] for p in waiting_players]}, room='commander', namespace='/')


@socketio.on('move')
def handle_move(data):
    sid = request.sid
    move = data['move']
    player_data = players.get(sid)

    if not player_data:
        socketio.emit('message', {'msg': 'Player not found.'}, room=sid, namespace='/')
        return
    if not player_data.get('turn'):
        socketio.emit('message', {'msg': 'Not your turn.'}, room=sid, namespace='/')
        return
    if player_data.get('ready_for_next_game'):
        socketio.emit('message', {'msg': 'Your game has concluded. Waiting for next round.'}, room=sid, namespace='/')
        return

    opponent_sid = player_data.get('opponent')
    if not opponent_sid or opponent_sid not in players:
        socketio.emit('message', {'msg': 'No opponent found or opponent disconnected.'}, room=sid, namespace='/')
        # Mark player as ready for next game if opponent is gone
        player_data['ready_for_next_game'] = True
        # Mark this specific game as completed if it was part of games_in_current_round
        game_id_prefix = player_data['game_log'].split(':')[0]
        if game_id_prefix in games_in_current_round:
            games_in_current_round[game_id_prefix]['completed'] = True
        check_round_completion() # Check if this makes the round complete
        return

    game_log = player_data['game_log']
    move_symbol = 'x' if move == 'take' else ('2' if random.random() < 0.25 else '0')
    if ':' not in game_log:
        socketio.emit('message', {'msg': 'Invalid game log format.'}, room=sid, namespace='/')
        return

    base, moves = game_log.split(':')
    moves = moves + '|' + move_symbol if moves else move_symbol
    updated_log = f"{base}:{moves}"
    players[sid]['game_log'] = updated_log
    players[opponent_sid]['game_log'] = updated_log

    turn_number = strip_game_log(updated_log)
    score = linear_score(turn_number)

    socketio.emit('update', {'score': score, 'log': updated_log}, room=sid, namespace='/')
    socketio.emit('update', {'score': score, 'log': updated_log}, room=opponent_sid, namespace='/')

    if move_symbol == 'x':
        save_game_log(updated_log, sid, opponent_sid, score)
        players[sid]['turn'] = False
        players[opponent_sid]['turn'] = False
        players[sid]['ready_for_next_game'] = True # Mark player as finished for this game
        players[opponent_sid]['ready_for_next_game'] = True # Mark opponent as finished for this game

        # Emit dedicated 'game_over' event
        socketio.emit('game_over', {'msg': 'You took the last item!', 'final_score': score, 'final_log': updated_log}, room=sid, namespace='/')
        socketio.emit('game_over', {'msg': 'Your opponent took the last item!', 'final_score': score, 'final_log': updated_log}, room=opponent_sid, namespace='/')

        # Still send a generic message for general notifications (e.g., "Waiting for next round...")
        socketio.emit('message', {'msg': 'Waiting for next round...'}, room=sid, namespace='/')
        socketio.emit('message', {'msg': 'Waiting for next round...'}, room=opponent_sid, namespace='/')

        # Mark this specific game as completed
        game_id = base
        if game_id in games_in_current_round:
            games_in_current_round[game_id]['completed'] = True

        # Check if the entire round is completed
        check_round_completion()

    else:
        # Toggle turns
        players[sid]['turn'] = False
        players[opponent_sid]['turn'] = True
        socketio.emit('message', {'msg': 'Waiting for opponent...'}, room=sid, namespace='/')
        socketio.emit('message', {'msg': 'Your turn! Choose a move:'}, room=opponent_sid, namespace='/')

def check_round_completion():
    global current_round_index

    # If there are no games being tracked, it means all were byes or disconnected, so round is complete
    if not games_in_current_round:
        all_games_completed_in_round = True
    else:
        all_games_completed_in_round = True
        for game_info in games_in_current_round.values():
            if not game_info['completed']:
                all_games_completed_in_round = False
                break

    if all_games_completed_in_round:
        socketio.emit('message', {'msg': f'Round {current_round_index + 1} completed!'}, room='commander', namespace='/')
        current_round_index += 1
        # Use a short delay to allow clients to process the "Round X completed" message
        socketio.sleep(2) # Non-blocking sleep
        socketio.start_background_task(target=play_next_round)


# --- Run App ---
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5001)
