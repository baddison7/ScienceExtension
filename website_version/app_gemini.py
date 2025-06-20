from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room
import random, os, datetime, time
import threading

app = Flask(__name__) # Using __app_id for the Flask app name
socketio = SocketIO(app)

# Session log setup
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)
session_log_filename = f"session_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
session_log_path = os.path.join(log_dir, session_log_filename)
name_log_filename = f"name_log_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
name_log_path = os.path.join(log_dir, name_log_filename)
score_log_filename = f"totalscore_log_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
score_log_path = os.path.join(log_dir, score_log_filename)


# Data structures
# players: sid -> {'name': str, 'opponent': sid, 'turn': bool, 'in_game': bool,
#                   'ready_for_next_game': bool, 'total_score': int,
#                   'played_with': set(sid), 'game_log': str, 'player_num': str}
players = {}
# ready_to_match: list of SIDs that are available for a new game and have not exhausted all possible unique opponents.
ready_to_match = []
# game_match_lock: A lock to prevent race conditions when multiple events try to modify ready_to_match
# or initiate games simultaneously.
game_match_lock = threading.Lock()


# --- Log Helpers ---
def update_total_score_log(sid, total_score):
    """
    Updates the total score for a player in the score log file.
    It reads all lines, removes the old entry for the given SID,
    and appends the new one, then writes back to the file.
    """
    if not os.path.exists(score_log_path):
        with open(score_log_path, 'w') as f:
            pass  # Just create the file if it doesn't exist

    with open(score_log_path, 'r') as f:
        lines = f.readlines()

    # Remove any previous entry for this sid
    lines = [line for line in lines if not line.startswith(sid)]

    # Add new score entry
    lines.append(f"{sid}:{total_score}\n")

    # Write back to file
    with open(score_log_path, 'w') as f:
        f.writelines(lines)

def save_game_log(game_log, sid1, sid2, final_score_tuple):
    """
    Appends the completed game's log to the session log file.
    Converts the game log format for cleaner storage.
    """
    game_id, moves = game_log.split(':')
    # Replace '|' with comma for better readability in logs, '0' and '2' with 'P', 'x' with 'T'
    moves_for_log = moves.replace('|', ',').replace('0', 'P').replace('2', 'P').replace('x', 'T')
    # Assuming final_score_tuple is (player1_score, player2_score) from their perspective
    p1_score, p2_score = final_score_tuple
    with open(session_log_path, 'a') as f:
        f.write(f"Game ID: {game_id}, P1_SID: {sid1}, P2_SID: {sid2}, Moves: [{moves_for_log}], "
                f"P1_Final_Score: {p1_score}, P2_Final_Score: {p2_score}\n")


# --- Game Logic Helpers ---
def linear_payoff(turn_number, p1_start=2, p2_start=1, increment=2):
    """
    Calculates the linear payoff for Player 1 and Player 2 based on the turn number.
    Turn number is the number of moves made in the game.
    """
    if turn_number < 1:
        return p1_start, p2_start
    p1 = p1_start + ((turn_number - 1) // 2) * increment
    p2 = p2_start + ((turn_number) // 2) * increment
    return p1, p2

def exponential_payoff(turn_number, p1_base=2, p2_base=1, growth_rate=1.5):
    """
    Calculates the exponential payoff for Player 1 and Player 2 based on the turn number.
    (Currently not used, but kept for potential future use or as an example).
    """
    if turn_number < 1:
        return p1_base, p2_base
    p1 = int(p1_base * (growth_rate ** ((turn_number - 1) // 2)))
    p2 = int(p2_base * (growth_rate ** ((turn_number) // 2)))
    return p1, p2

def strip_game_log(game_log):
    """
    Parses the game_log string to return the number of moves made.
    Game log format: "game_id:move1|move2|...|moveN"
    """
    try:
        parts = game_log.split(':', 1) # Split only on the first colon
        if len(parts) < 2: # No moves yet, just the game_id
            return 0
        moves_str = parts[1]
        return len(moves_str.split('|')) if moves_str else 0
    except ValueError:
        return 0

def get_player_name_display(sid):
    """
    Returns the first 4 characters of the player's name for display purposes.
    (This function is retained but its use for internal logging is removed.)
    """
    return players.get(sid, {}).get('name', sid[:4])[:4]

# --- Routes ---
@app.route('/')
def index():
    """
    Renders the main game page.
    """
    return render_template('index.html')

@app.route('/commander')
def commander():
    """
    Renders the commander/admin page to monitor players and trigger games.
    """
    return render_template('commander.html')

# --- SocketIO Events ---

@socketio.on('commander_start')
def commander_start():
    """
    Triggered by the commander to start the initial matching process.
    This will attempt to match any players currently in the 'ready_to_match' pool.
    Subsequent matches will occur automatically.
    """
    print("Commander initiated game matching.")
    # Ensure all players are marked as ready for the first round of matching
    with game_match_lock:
        for sid in list(players.keys()): # Iterate over a copy as dict may change
            if not players[sid]['in_game'] and sid not in ready_to_match:
                ready_to_match.append(sid)
                players[sid]['ready_for_next_game'] = True # Explicitly mark as ready

    socketio.start_background_task(target=attempt_matches)


@socketio.on('commander_join')
def commander_join():
    """
    Handles a commander joining the 'commander' room.
    Emits the current list of players to the commander.
    """
    join_room('commander')
    # Use player names for the commander's display
    player_names = [players[sid]['name'] for sid in players if 'name' in players[sid]]
    socketio.emit('update_players', {'players': player_names}, room='commander', namespace='/')
    socketio.emit('message', {'msg': 'Commander joined and is monitoring.'}, room='commander', namespace='/')


@socketio.on('join')
def handle_join(data):
    """
    Handles a new player joining the game.
    Initializes player data and adds them to the ready_to_match pool.
    """
    sid = request.sid
    name = data.get('name', f'Player_{sid[:4]}') # Default name if not provided
    with open(name_log_path, 'a') as f:
        f.write(f"{sid}: {name}\n") # Log name with SID

    # Initialize player data
    players[sid] = {
        'name': name, # Name is stored, but its usage is restricted for privacy in game logic
        'game_log': '',
        'opponent': None,
        'turn': False,
        'in_game': False,
        'ready_for_next_game': True, # Ready to be matched initially
        'total_score': 0,
        'played_with': set() # Keep track of opponents this player has already played against
    }

    # Add player to the ready_to_match pool if not already there and not in a game
    with game_match_lock:
        if not players[sid]['in_game'] and sid not in ready_to_match:
            ready_to_match.append(sid)
            print(f"Player {sid[:4]} joined and is ready to match. Ready count: {len(ready_to_match)}")

    # Updated message to reflect that only the initial games require commander start
    socketio.emit('message', {'msg': f'Welcome, {name}! Waiting for the first game to start...'}, room=sid, namespace='/')
    # Update commander with current player list
    player_names = [players[p_sid]['name'] for p_sid in players if 'name' in players[p_sid]]
    socketio.emit('update_players', {'players': player_names}, room='commander', namespace='/')
    # Games will only start via commander_start for the initial set.


def _start_game(p1_sid, p2_sid):
    """
    Helper function to set up and start a new game between two players.
    This encapsulates the common logic for initiating a game.
    """
    # Ensure players are still connected
    if p1_sid not in players or p2_sid not in players:
        print(f"Cannot start game: one or both SIDs {p1_sid[:4]}, {p2_sid[:4]} disconnected.")
        # If one disconnected, the other should be put back into ready_to_match
        if p1_sid in players and not players[p1_sid]['in_game']: # If p1 is not yet in a game, put them back
             with game_match_lock:
                 if p1_sid not in ready_to_match: ready_to_match.append(p1_sid)
        if p2_sid in players and not players[p2_sid]['in_game']: # If p2 is not yet in a game, put them back
             with game_match_lock:
                 if p2_sid not in ready_to_match: ready_to_match.append(p2_sid)
        return

    # Assign opponents and mark as in-game
    players[p1_sid]['opponent'] = p2_sid
    players[p2_sid]['opponent'] = p1_sid
    players[p1_sid]['in_game'] = True
    players[p2_sid]['in_game'] = True
    players[p1_sid]['ready_for_next_game'] = False # Not ready until game is over
    players[p2_sid]['ready_for_next_game'] = False # Not ready until game is over

    # Assign player numbers and set up game log
    players[p1_sid]['player_num'] = 'p1'
    players[p2_sid]['player_num'] = 'p2'
    short_game_id = f"{p1_sid[:2]}{p2_sid[:2]}" # Unique ID for this specific game instance
    game_log = f"{short_game_id}:"
    players[p1_sid]['game_log'] = game_log
    players[p2_sid]['game_log'] = game_log

    # Determine initial scores
    current_score = linear_payoff(0) # Before any moves, turn_number is 0
    expected_score_after_first_move = linear_payoff(1) # Score if the first player passes

    # Player 1 (p1_sid) always starts
    players[p1_sid]['turn'] = True
    players[p2_sid]['turn'] = False

    print(f"Starting game between {p1_sid[:4]} and {p2_sid[:4]}. Game ID: {short_game_id}")

    # Emit 'start' event to both players with their respective scores and messages
    socketio.emit('start', {
        'game_log': game_log,
        'your_score': expected_score_after_first_move[0], # P1 expects to pass for this score
        'opponents_score': expected_score_after_first_move[1], # P2 expects this if P1 passes
        'round': 1 # For dynamic matching, rounds aren't explicit, but can use 1 as a default
    }, room=p1_sid, namespace='/')
    socketio.emit('start', {
        'game_log': game_log,
        'your_score': expected_score_after_first_move[1], # P2 expects this if P1 passes
        'opponents_score': expected_score_after_first_move[0], # P1 expects to pass for this score
        'round': 1
    }, room=p2_sid, namespace='/')

    socketio.emit('message', {'msg': 'Your turn! Choose a move:'}, room=p1_sid, namespace='/')
    socketio.emit('message', {'msg': 'Waiting for opponent...'}, room=p2_sid, namespace='/')


def attempt_matches():
    """
    Attempts to find and start games for players in the 'ready_to_match' pool.
    It prioritizes "perfect stranger matching" by looking for players who haven't
    played against each other before.
    """
    global ready_to_match

    # Acquire lock to ensure atomic operations on ready_to_match and player states
    with game_match_lock:
        # Filter out disconnected players from ready_to_match
        ready_to_match = [sid for sid in ready_to_match if sid in players and not players[sid]['in_game']]

        # Shuffle the list to ensure fairness and reduce bias in matching order
        random.shuffle(ready_to_match)

        matched_pairs_for_this_run = []
        # Iterate through the shuffled list to find pairs
        for i in range(len(ready_to_match)):
            p1_sid = ready_to_match[i]
            # Ensure p1_sid is still valid and ready to be matched
            if p1_sid not in players or not players[p1_sid]['ready_for_next_game'] or players[p1_sid]['in_game']:
                continue

            found_match = False
            for j in range(i + 1, len(ready_to_match)):
                p2_sid = ready_to_match[j]
                # Ensure p2_sid is still valid and ready to be matched
                if p2_sid not in players or not players[p2_sid]['ready_for_next_game'] or players[p2_sid]['in_game']:
                    continue

                # Check if they haven't played before (perfect stranger matching)
                if p2_sid not in players[p1_sid]['played_with'] and p1_sid not in players[p2_sid]['played_with']:
                    matched_pairs_for_this_run.append((p1_sid, p2_sid))
                    found_match = True
                    # Mark players as "in-game" right away to prevent double matching in this loop
                    players[p1_sid]['in_game'] = True
                    players[p2_sid]['in_game'] = True
                    break # Found a match for p1_sid, move to next p1_sid in outer loop

            if found_match:
                continue # Move to the next player to try and match

        # Now, process the matched pairs outside the main iteration
        for p1_sid, p2_sid in matched_pairs_for_this_run:
            # Remove matched players from the ready_to_match list
            ready_to_match = [sid for sid in ready_to_match if sid not in [p1_sid, p2_sid]]

            # Add to played_with sets
            players[p1_sid]['played_with'].add(p2_sid)
            players[p2_sid]['played_with'].add(p1_sid)

            # Start the game (this part should be non-blocking, so put in background task)
            socketio.start_background_task(target=_start_game, p1_sid=p1_sid, p2_sid=p2_sid)
            print(f"Attempting to start game between {p1_sid[:4]} and {p2_sid[:4]}. "
                  f"Remaining ready players: {len(ready_to_match)}")

        # Logic for when no new matches were found in this attempt
        if not matched_pairs_for_this_run and ready_to_match: # Only message if there are players still waiting
            print("No new 'perfect stranger' matches found in this attempt.")
            
            # Get a snapshot of currently active and available players for matching
            current_active_player_sids = {sid for sid in players if not players[sid]['in_game'] and players[sid]['ready_for_next_game']}
            
            for p_sid in list(ready_to_match): # Iterate over a copy of ready_to_match
                if p_sid not in players: # Skip if player disconnected while loop was running
                    continue

                # Determine all *other* active players this specific player could potentially match with
                all_possible_opponents_for_p_sid = current_active_player_sids - {p_sid}

                # Check if the player has played with all possible unique opponents
                # This condition covers both having played everyone AND not being the only player remaining.
                if len(all_possible_opponents_for_p_sid) > 0 and players[p_sid]['played_with'].issuperset(all_possible_opponents_for_p_sid):
                    # This player has played with every other active player at least once.
                    socketio.emit('message', {'msg': 'You have played all possible unique matches with current players. Waiting for new players or for other games to finish.'}, room=p_sid, namespace='/')
                    print(f"Player {p_sid[:4]} has exhausted all unique opponents among active players.")
                elif len(current_active_player_sids) <= 1:
                     # This covers cases where there are 0 or 1 available players in total.
                    socketio.emit('message', {'msg': 'Waiting for more players to join for a new match.'}, room=p_sid, namespace='/')
                else:
                    # Generic waiting message if matches *could* still be made but weren't in this run
                    socketio.emit('message', {'msg': 'No new opponent found for you at this time. Please wait.'}, room=p_sid, namespace='/')
            
        # Inform commander about current waiting players
        waiting_player_names = [players[sid]['name'] for sid in ready_to_match if sid in players]
        socketio.emit('update_players', {'players': waiting_player_names}, room='commander', namespace='/')


@socketio.on('move')
def handle_move(data):
    """
    Handles a player's move ('take' or 'pass').
    Updates game state, scores, and communicates with players.
    """
    sid = request.sid
    move = data['move']
    player_data = players.get(sid)

    if not player_data or not player_data.get('in_game') or not player_data.get('turn'):
        # Ignore move if player not found, not in game, or not their turn
        print(f"Invalid move from {sid[:4]}: Not in game, or not their turn, or player data missing.")
        return

    opponent_sid = player_data.get('opponent')
    if not opponent_sid or opponent_sid not in players or not players[opponent_sid].get('in_game'):
        # Opponent disconnected or no longer in game. End current player's game.
        # This provides direct feedback to the player whose opponent is gone.
        socketio.emit('message', {'msg': 'Opponent disconnected. Your game has ended. Searching for a new match...'}, room=sid, namespace='/')
        player_data['in_game'] = False
        player_data['ready_for_next_game'] = True # Ready for next match
        # If the opponent disconnected, we should make this player available for a new match immediately.
        with game_match_lock:
            if sid not in ready_to_match:
                ready_to_match.append(sid)
        socketio.start_background_task(target=attempt_matches) # Attempt new match automatically
        return

    game_log = player_data['game_log']
    base_game_id, moves_so_far = game_log.split(':', 1) # Ensure we only split on the first colon
    
    # Determine the move symbol: 'x' for take, '0' or '2' for pass (random chance for '2')
    move_symbol = 'x' if move == 'take' else ('2' if random.random() < 0.25 else '0')
    
    # Append new move to the game log
    updated_moves_str = moves_so_far + '|' + move_symbol if moves_so_far else move_symbol
    updated_log = f"{base_game_id}:{updated_moves_str}"

    # Update game logs for both players
    players[sid]['game_log'] = updated_log
    players[opponent_sid]['game_log'] = updated_log

    # Calculate turn number based on the updated log
    turn_number = strip_game_log(updated_log)
    
    # Calculate current scores (what they receive if someone takes the pot now)
    current_payoff_tuple = linear_payoff(turn_number)
    # Calculate next expected scores (what they'd get if the game continues)
    expected_payoff_tuple = linear_payoff(turn_number + 1)

    # Prepare UI log: replace internal symbols with emojis for display
    ui_log_display = updated_moves_str.replace('|', '')
    ui_log_display = ui_log_display.replace('0', '🟩')
    ui_log_display = ui_log_display.replace('2', '🟩')
    ui_log_display = ui_log_display.replace('x', '🟥')

    # Helper to get scores from the perspective of a specific player (p1 vs p2)
    def get_player_perspective_scores(player_sid, p_payoff_tuple):
        return (p_payoff_tuple[0], p_payoff_tuple[1]) if players[player_sid]['player_num'] == 'p1' else \
               (p_payoff_tuple[1], p_payoff_tuple[0])

    # Scores for the current player's perspective
    your_current_score, your_opponent_current_score = get_player_perspective_scores(sid, current_payoff_tuple)
    your_expected_score, your_opponent_expected_score = get_player_perspective_scores(sid, expected_payoff_tuple)

    # Scores for the opponent's perspective
    opp_current_score, opp_opponent_current_score = get_player_perspective_scores(opponent_sid, current_payoff_tuple)
    opp_expected_score, opp_opponent_expected_score = get_player_perspective_scores(opponent_sid, expected_payoff_tuple)


    if move_symbol == 'x': # Player chose to 'take' the pot
        print(f"Game {base_game_id}: {sid[:4]} took the pot. Moves: {updated_moves_str}")

        # Save game log to file
        save_game_log(updated_log, sid, opponent_sid, current_payoff_tuple)

        # Mark players as no longer in game and ready for next match
        players[sid]['turn'] = False
        players[opponent_sid]['turn'] = False
        players[sid]['in_game'] = False
        players[opponent_sid]['in_game'] = False
        players[sid]['ready_for_next_game'] = True
        players[opponent_sid]['ready_for_next_game'] = True

        # Update total scores and log them
        players[sid]['total_score'] += your_current_score
        players[opponent_sid]['total_score'] += your_opponent_current_score
        update_total_score_log(sid, players[sid]['total_score'])
        update_total_score_log(opponent_sid, players[opponent_sid]['total_score'])

        print(f"Total scores: {sid[:4]}: {players[sid]['total_score']}, "
              f"{opponent_sid[:4]}: {players[opponent_sid]['total_score']}")

        # Emit game over messages to both players
        socketio.emit('game_over', {
            'msg': 'Game Over, you took the pot!',
            'winner': 'true', # From their perspective
            'your_score': your_current_score,
            'opponents_score': your_opponent_current_score,
            'final_log': ui_log_display,
            'total_score': players[sid]['total_score'] # Send total score to client
        }, room=sid, namespace='/')

        socketio.emit('game_over', {
            'msg': 'Game Over, your opponent took the pot!',
            'winner': 'false', # From their perspective
            'your_score': opp_current_score,
            'opponents_score': opp_opponent_current_score,
            'final_log': ui_log_display,
            'total_score': players[opponent_sid]['total_score'] # Send total score to client
        }, room=opponent_sid, namespace='/')

        # Updated message to reflect automatic re-matching
        socketio.emit('message', {'msg': 'Game over. Searching for a new match...'}, room=sid, namespace='/')
        socketio.emit('message', {'msg': 'Game over. Searching for a new match...'}, room=opponent_sid, namespace='/')

        # Add players back to the ready_to_match pool for dynamic matching
        with game_match_lock:
            if sid not in ready_to_match:
                ready_to_match.append(sid)
            if opponent_sid not in ready_to_match:
                ready_to_match.append(opponent_sid)
        
        # Now, automatically attempt to match new games
        socketio.start_background_task(target=attempt_matches)

    else: # Player chose to 'pass'
        print(f"Game {base_game_id}: {sid[:4]} passed. Moves: {updated_moves_str}")

        # Emit update to both players with new scores and log
        socketio.emit('update', {
            'your_score': your_expected_score,
            'opponents_score': your_opponent_expected_score,
            'log': ui_log_display,
        }, room=sid, namespace='/')

        socketio.emit('update', {
            'your_score': opp_expected_score,
            'opponents_score': opp_opponent_expected_score,
            'log': ui_log_display,
        }, room=opponent_sid, namespace='/')

        # Switch turns
        players[sid]['turn'] = False
        players[opponent_sid]['turn'] = True
        socketio.emit('message', {'msg': 'Waiting for opponent...'}, room=sid, namespace='/')
        socketio.emit('message', {'msg': 'Your turn! Choose a move:'}, room=opponent_sid, namespace='/')

# --- Run App ---
if __name__ == '__main__':
    # When running locally without a Canvas environment, __app_id might not be defined.
    # We can use a default Flask app name in that case.
    app.config['SECRET_KEY'] = 'a_secret_key_for_flask_sessions' # Necessary for SocketIO
    print("Starting Flask SocketIO server...")
    socketio.run(app, host='0.0.0.0', port=5001, debug=True, allow_unsafe_werkzeug=True) # debug=True for local development
