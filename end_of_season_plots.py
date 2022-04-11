"""Tweet a thread with the playoff team's wins and injuries for both conferences.

Usage:
  end_of_season_plots.py

Options:
  -h --help         Show this screen.
"""
from datetime import date

from docopt import docopt
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
import tweepy

from basketball_reference_scraper.constants import TEAM_TO_TEAM_ABBR as TEAM2ABBR
from basketball_reference_scraper.seasons import get_schedule, get_standings
from basketball_reference_scraper.injury_report import get_injury_report
from basketball_reference_scraper.utils import remove_accents

from credentials import API_KEY, API_SECRET_KEY, ACCESS_TOKEN, ACCESS_TOKEN_SECRET
from nba_game_plots import shorten, red, blue


auth = tweepy.OAuthHandler(API_KEY, API_SECRET_KEY)
auth.set_access_token(ACCESS_TOKEN, ACCESS_TOKEN_SECRET)
API = tweepy.API(auth)

year = date.today().year
standings = get_standings()
schedule = get_schedule(year)
injury_report = get_injury_report()

for conference_standings in standings.values():
    api_reply = None
    for team in conference_standings.iloc[:10].itertuples():
        team_abbr = TEAM2ABBR[team.TEAM.upper()]
        injuries = injury_report.query(f"TEAM == '{team_abbr}'")
        if len(injuries):
            status = "\n".join(
                injuries.apply(
                    lambda injury: " ".join(
                        [
                            shorten(remove_accents(injury.PLAYER, team_abbr, year)),
                            injury.STATUS,
                            str(injury.DATE.date()),
                            injury.INJURY,
                        ]
                    ),
                    axis=1,
                )
            )
        else:
            status = ""

        games = pd.concat(
            [
                schedule.query(f"VISITOR == '{team.TEAM}'")
                .set_index("DATE")
                .eval("VISITOR_PTS > HOME_PTS"),
                schedule.query(f"HOME == '{team.TEAM}'")
                .set_index("DATE")
                .eval("VISITOR_PTS < HOME_PTS"),
            ]
        ).sort_index()

        plt.figure(figsize=[5.05, 2.85])
        games.cumsum().plot(color=blue)
        plt.vlines(
            injuries[injuries["DATE"] > games.index.min()].DATE,
            0,
            games.sum(),
            color=red,
            linestyle=":",
        )
        sns.despine()
        plt.xlabel("")
        plt.title(f"{team.Index + 1}. {team.TEAM}, {games.sum()} in {len(games)}")
        plt.savefig("season.png", transparent=False, dpi=300)

        media = API.media_upload("season.png")
        api_reply = API.update_status(
            status[:279],
            media_ids=[media.media_id],
            in_reply_to_status_id=api_reply.id_str if api_reply else None,
        )

if __name__ == "__main__":
    arguments = docopt(__doc__)
