def strip_game_log(game_log):
    ids, nodes = game_log.split('_')
    length = len(nodes)
    count = 0
    for i in nodes:
        if i == '1':
            count += 1
    turns = length - count
    return turns

print(strip_game_log('0000_'))