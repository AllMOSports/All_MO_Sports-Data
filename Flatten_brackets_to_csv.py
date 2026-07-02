"""
flatten_brackets_to_csv.py
 
Converts the nested JSON output of scrape_district_basketball_brackets.py
into a flat, one-row-per-game CSV -- ready to drop into Excel or feed
into your existing district team-records workbook pipeline.
 
Usage:
  python flatten_brackets_to_csv.py \
      --input boys_basketball_district_brackets_2012-2026.json \
      --output boys_basketball_district_games_2012-2026.csv
"""
 
import argparse
import csv
import json
from pathlib import Path
 
 
FIELDNAMES = [
    "season_label", "year", "class", "district", "round", "game_num",
    "date_time", "tournament_id",
    "team1", "team1_id", "team1_seed", "team1_score", "team1_winner",
    "team2", "team2_id", "team2_seed", "team2_score", "team2_winner",
    "margin", "champion", "champion_id", "incomplete",
]
 
 
def flatten(records: list[dict]) -> list[dict]:
    rows = []
    for bracket in records:
        for game in bracket["games"]:
            row = {
                "season_label": bracket["season_label"],
                "year": bracket["year"],
                "class": bracket["class"],
                "district": bracket["district"],
                "round": game.get("round"),
                "game_num": game.get("game_num"),
                "date_time": game.get("date_time"),
                "tournament_id": game.get("tournament_id"),
                "champion": bracket.get("champion"),
                "champion_id": bracket.get("champion_id"),
                "incomplete": game.get("incomplete", False),
            }
            if game.get("incomplete"):
                # Bye or malformed game -- team1/team2 fields left blank,
                # raw team list preserved for manual review.
                row["team1"] = None
                row["team2"] = None
                for f in ["team1_id", "team1_seed", "team1_score", "team1_winner",
                          "team2_id", "team2_seed", "team2_score", "team2_winner", "margin"]:
                    row[f] = None
            else:
                row.update({
                    "team1": game["team1"], "team1_id": game["team1_id"],
                    "team1_seed": game["team1_seed"], "team1_score": game["team1_score"],
                    "team1_winner": game["team1_winner"],
                    "team2": game["team2"], "team2_id": game["team2_id"],
                    "team2_seed": game["team2_seed"], "team2_score": game["team2_score"],
                    "team2_winner": game["team2_winner"],
                })
                try:
                    row["margin"] = abs(int(game["team1_score"]) - int(game["team2_score"]))
                except (TypeError, ValueError):
                    row["margin"] = None
            rows.append(row)
    return rows
 
 
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
 
    with open(args.input, "r", encoding="utf-8") as f:
        records = json.load(f)
 
    rows = flatten(records)
    rows.sort(key=lambda r: (r["year"], r["class"], r["district"], r["game_num"]))
 
    output_path = Path(args.output)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
 
    print(f"Wrote {len(rows)} game rows from {len(records)} brackets -> {output_path}")
 
 
if __name__ == "__main__":
    main()
