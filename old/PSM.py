import random
from itertools import combinations

def generate_PSM(players):
    try:
        if len(players) % 2 != 0:
            raise ValueError("Number of players must be even")

        # All unique player pairs
        all_pairs = set(combinations(players, 2))
        used_pairs = set()
        rounds = []

        # Number of rounds needed: (n choose 2) / (n / 2) = n - 1
        max_rounds = len(players) - 1

        for _ in range(max_rounds):
            available_pairs = list(all_pairs - used_pairs)
            random.shuffle(available_pairs)

            round_pairs = []
            used_players = set()

            for pair in available_pairs:
                a, b = pair
                if a in used_players or b in used_players:
                    continue
                round_pairs.append(pair)
                used_players.update([a, b])
                used_pairs.add(pair)

                if len(used_players) == len(players):
                    break

            if len(round_pairs) != len(players) // 2:
                raise Exception("Could not find valid non-repeating pairings.")

            rounds.append(round_pairs)

        return rounds
    except:
        return generate_PSM(players)


# players = ['P1', 'P2', 'P3', 'P4']
# rounds = generate_PSM(players)
# print(rounds)
# for i, rnd in enumerate(rounds):
#     print(f"Round {i+1}: {rnd}")
