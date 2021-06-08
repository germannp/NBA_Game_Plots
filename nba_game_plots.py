"""Tweet plots and stats of NBA games.

Usage:
  nba_game_plots.py (-h | --help)
  nba_game_plots.py --date 2021-05-22
  nba_game_plots.py --interval 4

Options:
  -h --help           Show this screen.
  --date=<date>       ISO-formated date to tweet about games of.
  --interval=<hours>  Check and tweet new games of today, yesterday, and the day before yesterday.
"""
from datetime import date, timedelta
import time

from docopt import docopt
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import tweepy

from basketball_reference_web_scraper import client
from basketball_reference_web_scraper.data import Location
from basketball_reference_web_scraper.data import TEAM_TO_TEAM_ABBREVIATION as TEAM2ABRV
from basketball_reference_web_scraper.errors import InvalidDate


# Use credentials.py locally and env variables in the cloud
try:
    from credentials import API_KEY, API_SECRET_KEY, ACCESS_TOKEN, ACCESS_TOKEN_SECRET

except ModuleNotFoundError:
    from os import environ

    API_KEY = environ["API_KEY"]
    API_SECRET_KEY = environ["API_SECRET_KEY"]
    ACCESS_TOKEN = environ["ACCESS_TOKEN"]
    ACCESS_TOKEN_SECRET = environ["ACCESS_TOKEN_SECRET"]

auth = tweepy.OAuthHandler(API_KEY, API_SECRET_KEY)
auth.set_access_token(ACCESS_TOKEN, ACCESS_TOKEN_SECRET)
API = tweepy.API(auth)


def tweet_games_of_day(date=date.today()):
    try:
        player_scores = pd.DataFrame(
            client.player_box_scores(year=date.year, month=date.month, day=date.day)
        )
    except InvalidDate:
        print(f"Not yet {date} at basketball-reference.com")
        return

    if not len(player_scores):
        print(f"No games found on {date}")
        return

    player_scores["name"] = player_scores["name"].apply(
        lambda name: name[0] + ". " + name.split(" ")[-1]
    )
    player_scores["home_team"] = player_scores.apply(
        lambda player: player["team"]
        if player["location"] == Location.HOME
        else player["opponent"],
        axis=1,
    )
    player_scores["points"] = player_scores.eval(
        "made_free_throws + made_field_goals * 2 + made_three_point_field_goals"
    )
    player_scores["rebounds"] = player_scores.eval(
        "defensive_rebounds + offensive_rebounds"
    )

    for home_team in player_scores["home_team"].unique():
        game = player_scores[player_scores["home_team"] == home_team]

        # Title
        away_team = next(team for team in game["team"] if team != home_team)
        home_score = game[game["team"] == home_team]["points"].sum()
        away_score = game[game["team"] == away_team]["points"].sum()
        game_status = "#{}vs{} {}:{} on {}\n".format(
            TEAM2ABRV[away_team],
            TEAM2ABRV[home_team],
            away_score,
            home_score,
            date,
        )

        if API.search(f"from:{API.me().screen_name} '{game_status}'"):
            # This is not waterproof: It takes ~20s until a new tweet is found. If the app
            # is restarted meanwhilte, it will tweet again 🤷
            print(f"{game_status[:-1]} already tweeted")
            continue

        # Game stats
        scores = pd.DataFrame(
            client.play_by_play(
                home_team=home_team, year=date.year, month=date.month, day=date.day
            )
        )
        game_status += "Ties: {}\n".format(
            scores.drop_duplicates(subset=["away_score", "home_score"])
            .query("away_score > 0")
            .eval("away_score == home_score")
            .sum()
        )
        game_status += "Lead changes: {}\n".format(
            (scores.eval("away_score - home_score").replace(0, np.nan).dropna() < 0)
            .diff()
            .sum()
        )
        game_status += "Largest lead: {}\n".format(
            scores.eval("away_score - home_score").abs().max(),
        )

        scores["time"] = (
            np.minimum(scores["remaining_seconds_in_period"].diff(), 0)
            .cumsum()
            .fillna(0)
            / -60
        )
        scores["duration"] = scores["time"].diff().fillna(0)

        away_lead = pd.to_timedelta(
            scores.query("away_score > home_score")["duration"].sum(), unit="min"
        )
        game_status += "{} led: ~{}:{:02d}\n".format(
            TEAM2ABRV[scores["away_team"][0]],
            away_lead.components.minutes,
            away_lead.components.seconds,
        )
        home_lead = pd.to_timedelta(
            scores.query("away_score < home_score")["duration"].sum(), unit="min"
        )
        game_status += "{} led: ~{}:{:02d}".format(
            TEAM2ABRV[scores["home_team"][0]],
            home_lead.components.minutes,
            home_lead.components.seconds,
        )

        # Plot scores over time
        plt.figure(figsize=[5.05, 2.85])
        for location, color in {"away": "#1d428a", "home": "#c8102e"}.items():
            team = scores[f"{location}_team"][0]
            name = team.name.title().replace("_", " ")
            score = scores[f"{location}_score"].max()
            plt.plot(
                scores["time"],
                scores[f"{location}_score"],
                label=name + ", " + str(score),
                color=color,
            )

        pauses = [
            pause
            for pause in [12, 24, 36, 48, 53, 58, 63, 68, 73, 78, 83]
            if pause < scores["time"].max()
        ]
        pause_scores = [
            scores.query(f"time < {pause}")[["home_score", "away_score"]].max().max()
            for pause in pauses
        ]
        plt.vlines(pauses, 0, pause_scores, colors="0.8", linestyles=":")

        plt.title(date)
        plt.legend(frameon=False)
        plt.xlabel("Minutes")
        plt.ylabel("Scores")
        plt.xlim(left=0)
        plt.ylim(bottom=0)
        sns.despine()
        plt.tight_layout()
        plt.savefig("scores.png", transparent=False, dpi=300)
        media = API.media_upload("scores.png")
        api_reply = API.update_status(game_status[:280], media_ids=[media.media_id])

        # Team stats
        teams_status = ""
        total = game.groupby(lambda index: game.loc[index, "location"].name).apply(sum)
        for name, stat in {
            "FG": "field_goals",
            "3P": "three_point_field_goals",
            "FT": "free_throws",
        }.items():
            teams_status += "{}: {} of {} / {} of {}\n".format(
                name,
                total[f"made_{stat}"]["AWAY"],
                total[f"attempted_{stat}"]["AWAY"],
                total[f"made_{stat}"]["HOME"],
                total[f"attempted_{stat}"]["HOME"],
            )
        teams_status += "DRB: {} of {} / {} of {}\n".format(
            total["defensive_rebounds"]["AWAY"],
            total["defensive_rebounds"]["AWAY"] + total["offensive_rebounds"]["HOME"],
            total["defensive_rebounds"]["HOME"],
            total["defensive_rebounds"]["HOME"] + total["offensive_rebounds"]["AWAY"],
        )
        for name, stat in {
            "AST": "assists",
            "STL": "steals",
            "BLK": "blocks",
            "TOV": "turnovers",
            "PF": "personal_fouls",
        }.items():
            teams_status += f"{name}: {total[stat]['AWAY']} / {total[stat]['HOME']}\n"
        teams_status += (
            "\nSource & more data: "
            + "https://www.basketball-reference.com/boxscores/pbp/{}{:02d}{:02d}0{}.html".format(
                date.year, date.month, date.day, TEAM2ABRV[home_team]
            )
        )
        api_reply = API.update_status(
            teams_status[:280], in_reply_to_status_id=api_reply.id_str
        )

        # Best individual stats
        players_status = ""
        for stat in ["points", "rebounds", "assists", "steals", "blocks"]:
            players_status += (
                f"{stat.title()}: "
                + ", ".join(
                    game.sort_values(stat, ascending=False)
                    .apply(lambda player: f"{player['name']} {player[stat]}", axis=1)
                    .iloc[:3]
                )
                + "\n"
            )
        api_reply = API.update_status(
            players_status[:280], in_reply_to_status_id=api_reply.id_str
        )


if __name__ == "__main__":
    arguments = docopt(__doc__)

    if arguments["--date"]:
        tweet_games_of_day(date.fromisoformat(arguments["--date"]))

    if not arguments["--interval"]:
        exit()

    while True:
        for date_ in [date.today() + timedelta(days=i) for i in [-2, -1, 0]]:
            tweet_games_of_day(date_)
        time.sleep(float(arguments["--interval"]) * 60 * 60)
