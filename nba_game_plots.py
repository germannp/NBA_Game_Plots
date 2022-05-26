"""Tweet plots and stats of NBA games.

Usage:
  nba_game_plots.py
  nba_game_plots.py --date 2021-05-22
  nba_game_plots.py -h | --help

Options:
  --date=<date>     ISO-formated date to tweet about games of. If no date is given,
                    about the new games of the last three days is tweeted.
  -h --help         Show this screen.
"""
from itertools import product

from docopt import docopt
import matplotlib.pyplot as plt
from matplotlib.patches import Arc, Circle, Rectangle
import numpy as np
import pandas as pd
import seaborn as sns
import tweepy

from basketball_reference_scraper.constants import TEAM_TO_TEAM_ABBR as TEAM2ABBR
from basketball_reference_scraper.seasons import get_schedule
from basketball_reference_scraper.pbp import get_pbp
from basketball_reference_scraper.box_scores import get_box_scores
from basketball_reference_scraper.injury_report import get_injury_report
from basketball_reference_scraper.utils import remove_accents
from basketball_reference_scraper.shot_charts import get_shot_chart

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

blue = "#1d428a"
red = "#c8102e"


def shorten(name):
    first, *last = name.split(" ")
    if len(first) > 2 and any(c for c in first if c.islower()):
        first = first[0] + "."
    return " ".join([first] + last)


def tweet_game(game, injury_report):
    # Title
    date = game.DATE.date()
    away_team = game.VISITOR
    home_team = game.HOME
    away_abbr = TEAM2ABBR[away_team.upper()]
    home_abbr = TEAM2ABBR[home_team.upper()]
    away_score = int(game.VISITOR_PTS)
    home_score = int(game.HOME_PTS)
    game_status = f"#{away_abbr}vs{home_abbr} {away_score}:{home_score} on {date}"

    if API.search(f"from:{API.me().screen_name} '{game_status}'"):
        # This is not waterproof: It takes ~20s until a new tweet can be found. If the
        # app is run meanwhile, it will tweet again 🤷
        print(f"{game_status} already tweeted")
        return

    # Game stats
    play_by_play = get_pbp(date, away_abbr, home_abbr)
    play_by_play.columns = [
        "quarter",
        "time_remaining",
        "away_action",
        "home_action",
        "away_score",
        "home_score",
    ]

    game_status += "\nTies: {}\n".format(
        play_by_play.drop_duplicates(subset=["away_score", "home_score"])
        .query("away_score > 0")
        .eval("away_score == home_score")
        .sum()
    )
    play_by_play["lead"] = play_by_play["away_score"] - play_by_play["home_score"]
    game_status += "Lead changes: {}\n".format(
        (play_by_play["lead"].replace(0, np.nan).dropna() < 0).diff().sum()
    )
    game_status += "Largest lead: {}\n".format(
        play_by_play["lead"].abs().max(),
    )

    play_by_play["remaining_seconds_in_period"] = play_by_play["time_remaining"].apply(
        lambda time: int(time.split(":")[0]) * 60 + float(time.split(":")[1])
    )
    play_by_play["time"] = (
        np.minimum(play_by_play["remaining_seconds_in_period"].diff(), 0)
        .cumsum()
        .fillna(0)
        / -60
    )
    play_by_play["duration"] = play_by_play["time"].diff().fillna(0)

    away_lead = pd.to_timedelta(
        play_by_play.query("away_score > home_score")["duration"].sum(), unit="min"
    )
    game_status += "{} led: ~{}:{:02d}\n".format(
        away_abbr,
        away_lead.components.minutes,
        away_lead.components.seconds,
    )
    home_lead = pd.to_timedelta(
        play_by_play.query("away_score < home_score")["duration"].sum(), unit="min"
    )
    game_status += "{} led: ~{}:{:02d}".format(
        home_abbr,
        home_lead.components.minutes,
        home_lead.components.seconds,
    )

    # Plot scores over time
    plt.figure(figsize=[5.05, 2.85])
    for team, score, score_column, color in [
        (away_team, away_score, "away_score", blue),
        (home_team, home_score, "home_score", red),
    ]:
        plt.plot(
            play_by_play["time"],
            play_by_play[score_column],
            label=team + ", " + str(score),
            color=color,
        )

    pauses = [
        pause
        for pause in [12, 24, 36, 48, 53, 58, 63, 68, 73, 78, 83]
        if pause < play_by_play["time"].max()
    ]
    pause_play_by_play = [
        play_by_play.query(f"time < {pause}")[["home_score", "away_score"]].max().max()
        for pause in pauses
    ]
    plt.vlines(pauses, 0, pause_play_by_play, colors="0.8", linestyles=":")

    plt.title(date)
    plt.legend(frameon=False)
    plt.xlabel("Minutes")
    plt.ylabel("Points")
    plt.xlim(left=0)
    plt.ylim(bottom=0)
    sns.despine()
    plt.tight_layout()
    plt.savefig("scores.png", transparent=False, dpi=300)
    media = API.media_upload("scores.png")
    api_reply = API.update_status(game_status[:279], media_ids=[media.media_id])

    # Team stats
    try:
        box_scores = get_box_scores(date, away_abbr, home_abbr)
        away_totals = box_scores[away_abbr].iloc[-1]
        home_totals = box_scores[home_abbr].iloc[-1]
        teams_status = ""
        for stat in ["FG", "3P", "FT"]:
            teams_status += "{}: {} of {} / {} of {}\n".format(
                stat,
                away_totals[stat],
                away_totals[stat + "A"],
                home_totals[stat],
                home_totals[stat + "A"],
            )
        teams_status += "DRB: {} of {} / {} of {}\n".format(
            away_totals["DRB"],
            int(away_totals["DRB"]) + int(away_totals["ORB"]),
            home_totals["DRB"],
            int(home_totals["DRB"]) + int(home_totals["ORB"]),
        )
        for stat in ["AST", "STL", "BLK", "TOV", "PF"]:
            teams_status += "{}: {} / {}\n".format(
                stat,
                away_totals[stat],
                home_totals[stat],
            )
    except ValueError:
        box_scores = None
        teams_status = "Sorry, no box scores for this game 🤷"

    # Plot shot chart
    shots = get_shot_chart(date, away_abbr, home_abbr)
    shots[away_abbr]["TEAM"] = away_abbr
    shots[home_abbr]["TEAM"] = home_abbr
    shots = shots[away_abbr].append(shots[home_abbr])
    shots["x"] = shots["x"].apply(lambda ft: float(ft[:-3]))
    shots["y"] = shots["y"].apply(lambda ft: float(ft[:-3]))

    # Unfortunately the coordinates suck, so we shift and scale them around to make sure
    # all threes are from behind the ark.
    left_corner = shots.query("VALUE == 3 and y < 14 and x < 25")["x"].max()
    right_corner = shots.query("VALUE == 3 and y < 14 and x > 25")["x"].min()
    if left_corner and right_corner:
        shots["x"] = shots["x"] - left_corner
        shots["x"] = shots["x"] / (right_corner - left_corner) * 44
        shots["x"] = shots["x"] + 3
    behind_ark = shots.query("VALUE == 3 and y > 14")
    min_dist = np.sqrt(
        (behind_ark["x"] - 25) ** 2 + (behind_ark["y"] - 5.25) ** 2
    ).min()
    shots["y"] = shots["y"] / min_dist * 23.75

    plt.figure(figsize=[5.05, 2.85])
    plt.title(f"{away_team} {away_score}:{home_score} {home_team}")
    for make_miss, marker in [
        ("MAKE", "o"),
        ("MISS", "x"),
    ]:
        plt.scatter(
            shots.query(f"TEAM == '{away_abbr}' and MAKE_MISS == '{make_miss}'")["y"],
            shots.query(f"TEAM == '{away_abbr}' and MAKE_MISS == '{make_miss}'")["x"],
            marker=marker,
            ec=blue,
            fc="none",
        )
        plt.scatter(
            94
            - shots.query(f"TEAM == '{home_abbr}' and MAKE_MISS == '{make_miss}'")["y"],
            50
            - shots.query(f"TEAM == '{home_abbr}' and MAKE_MISS == '{make_miss}'")["x"],
            marker=marker,
            ec=red,
            fc="none",
        )

    plt.gca().add_artist(Circle((47, 25), 6, fc="none", ec="k", lw=1))
    plt.plot([47, 47], [0, 50], lw=1, c="k")

    plt.gca().add_artist(Circle((5.25, 25), 1.5, fc="none", ec="k", lw=1))
    plt.plot([0, 14], [3, 3], lw=1, c="k")
    plt.plot([0, 14], [47, 47], lw=1, c="k")
    plt.gca().add_artist(
        Arc((5.25, 25), 47.5, 47.5, theta1=292, theta2=68, fc="none", lw=1)
    )
    plt.gca().add_artist(Rectangle((0, 17), 19, 16, lw=1, ec="k", fill=False))

    plt.gca().add_artist(Circle((88.75, 25), 1.5, fc="none", ec="k", lw=1))
    plt.plot([80, 94], [3, 3], lw=1, c="k")
    plt.plot([80, 94], [47, 47], lw=1, c="k")
    plt.gca().add_artist(
        Arc((88.75, 25), 47.5, 47.5, theta1=112, theta2=249, fc="none", lw=1)
    )
    plt.gca().add_artist(Rectangle((77, 17), 19, 16, lw=1, ec="k", fill=False))

    plt.gca().set_aspect("equal")
    plt.xlim(0, 94)
    plt.ylim(0, 50)
    plt.xticks([])
    plt.yticks([])
    plt.tight_layout()
    plt.savefig("shots.png", transparent=False, dpi=300)
    media = API.media_upload("shots.png")
    api_reply = API.update_status(
        teams_status[:279],
        media_ids=[media.media_id],
        in_reply_to_status_id=api_reply.id_str,
    )

    # Best individual stats
    if box_scores:
        stats = ["PTS", "TRB", "AST", "STL", "BLK"]
        box_scores = (
            pd.concat(box_scores.values())
            .query("PLAYER != 'Team Totals'")
            .set_index("PLAYER")
        )
        box_scores = box_scores[
            ~box_scores["MP"].str.contains("Not")
            & ~box_scores["MP"].str.contains("Suspended")
        ].astype({stat: int for stat in stats})

        players_status = ""
        for stat in stats:
            players_status += (
                f"{stat}: "
                + ", ".join(
                    box_scores.sort_values(stat, ascending=False)
                    .iloc[:3]
                    .apply(
                        lambda player: f"{shorten(player.name)} {player[stat]}", axis=1
                    )
                )
                + "\n"
            )
        api_reply = API.update_status(
            players_status[:279], in_reply_to_status_id=api_reply.id_str
        )

    # Injury report
    injury_stati = []
    for team in [away_abbr, home_abbr]:
        team_injuries = injury_report[
            (injury_report["DATE"] <= pd.to_datetime(date))
            & (injury_report["TEAM"] == team)
        ]
        if len(team_injuries):
            injury_stati.append(
                team
                + ":\n"
                + "\n".join(
                    team_injuries.apply(
                        lambda injury: " ".join(
                            [
                                shorten(remove_accents(injury.PLAYER, team, date.year)),
                                injury.STATUS,
                                str(injury.DATE.date()),
                                injury.INJURY,
                            ]
                        ),
                        axis=1,
                    )
                )
            )
    if not injury_stati:
        return

    if len(injury_stati) == 2 and len(injury_stati[0]) + len(injury_stati[1]) <= 278:
        injury_stati = [injury_stati[0] + "\n" + injury_stati[1]]

    for status in injury_stati:
        api_reply = API.update_status(
            status[:279], in_reply_to_status_id=api_reply.id_str
        )

    # Link to Basketball Reference
    link_to_source = (
        "\nSource & more data: "
        + "https://www.basketball-reference.com/boxscores/pbp/{}{:02d}{:02d}0{}.html".format(
            date.year, date.month, date.day, TEAM2ABBR[home_team.upper()]
        )
    )
    api_reply = API.update_status(
        link_to_source[:279], in_reply_to_status_id=api_reply.id_str
    )


if __name__ == "__main__":
    arguments = docopt(__doc__)

    if arguments["--date"]:
        date = pd.to_datetime(arguments["--date"])
    else:
        date = pd.Timestamp.today()

    schedule = pd.DataFrame()
    for year, playoffs in product([date.year, date.year + 1], [False, True]):
        try:
            schedule = schedule.append(get_schedule(year, playoffs=playoffs).dropna())
        except ValueError:
            break  # No schedule available yet

    if arguments["--date"]:
        games = schedule.query(f"DATE == '{date.date()}'")
    else:
        games = schedule[(date - pd.to_timedelta("2d")) <= schedule["DATE"]]

    if not len(games):
        print(f"No games on {date.date()}")
        exit()

    injury_report = get_injury_report()
    for game in games.itertuples():
        tweet_game(game, injury_report)
