"""Monitor a subreddit for a phrase in recent posts."""

import json
import os
import time
from urllib.request import Request, urlopen

import praw
from prawcore.exceptions import PrawcoreException
from dotenv import load_dotenv


# Load variables from the .env file.
load_dotenv()


def alert(post, phrase, webhook_url):
    """Print an alert and optionally send it to Discord."""
    author = str(post.author) if post.author else "[deleted]"

    message = (
        f"Found {phrase!r} in r/{post.subreddit.display_name}\n"
        f"Author: u/{author}\n"
        f"Title: {post.title}\n"
        f"URL: https://reddit.com{post.permalink}"
    )

    print(f"\n{message}\n", flush=True)

    if webhook_url:
        request = Request(
            webhook_url,
            data=json.dumps({"content": message[:2000]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=10):
                pass
            print("Discord notification sent.", flush=True)
        except Exception as error:
            print(f"Discord notification failed: {error}", flush=True)


def main():
    # Read configuration from environment variables or .env.
    subreddit_name = os.getenv("SUBREDDIT", "").strip()
    phrase = os.getenv("MATCH_PHRASE", "").strip()
    client_id = os.getenv("REDDIT_CLIENT_ID", "").strip()
    client_secret = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
    user_agent = os.getenv(
        "REDDIT_USER_AGENT", "keyword-monitor/1.0"
    ).strip()
    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

    # Validate required configuration.
    if not all((subreddit_name, phrase, client_id, client_secret)):
        raise SystemExit(
            "Missing required configuration. Check your .env file for "
            "SUBREDDIT, MATCH_PHRASE, REDDIT_CLIENT_ID, and "
            "REDDIT_CLIENT_SECRET."
        )

    print(f"Subreddit: r/{subreddit_name}")
    print(f"Searching for: {phrase!r}")

    # Connect to Reddit.
    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
    )

    subreddit = reddit.subreddit(subreddit_name)

    # Six-minute window gives a one-minute overlap between scheduled runs.
    cutoff = time.time() - (6 * 60)

    checked = 0
    matches = 0

    try:
        # Newest posts first; stop when posts are older than the cutoff.
        for post in subreddit.new(limit=100):
            if post.created_utc < cutoff:
                break

            checked += 1
            text = f"{post.title}\n{post.selftext}"

            if phrase.casefold() in text.casefold():
                matches += 1
                alert(post, phrase, webhook_url)

    except PrawcoreException as error:
        raise SystemExit(f"Reddit API error: {error}") from error

    print(
        f"Scan complete. Checked {checked} recent posts; "
        f"found {matches} matches.",
        flush=True,
    )


if __name__ == "__main__":
    main()

