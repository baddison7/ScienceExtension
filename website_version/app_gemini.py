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
name_log_filename = f"name_log_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
name_log_path = os.path.join(log_dir, name_log_filename)


def linear_payoff(turn_number, p1_start=2, p2_start=1, increment=2):
    if turn_number < 1:
        return p1_start, p2_start  # Fallback if something weird happens
    p1 = p1_start + ((turn_number - 1) // 2) * increment
    p2 = p2_start + ((turn_number) // 2) * increment
    return p1, p2

def exponential_payoff(turn_number, p1_base=2, p2_base=1, growth_rate=1.5):
    if turn_number < 1:
        return p1_base, p2_base
    p1 = int(p1_base * (growth_rate ** ((turn_number - 1) // 2)))
    p2 = int(p2_base * (growth_rate ** ((turn_number) // 2)))
    return p1, p2

def strip_game_log(game_log):
    try:
        _, moves = game_log.split(':')
        return len(moves.split('|')) if moves else 1
    except ValueError:
        return 0

def save_game_log(game_log, sid1, sid2, final_score):
    game_id, moves = game_log.split(':')
    moves = moves.replace('|', '')  # Use comma for better readability in logs
    with open(session_log_path, 'a') as f:
        f.write(f"{sid1}:{sid2}|{moves}\n")

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
    pass

@socketio.on('commander_join')
def commander_join():
    join_room('commander')

@socketio.on('join')
def handle_join(data):
    sid = request.sid
    name = data.get('name', f'Player_{sid[:4]}')   # Default name if not provided
    with open(name_log_path, 'a') as f:
        f.write(f"{sid}: {name}\n")




# --- Run App ---
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5001)
