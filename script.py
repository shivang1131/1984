"""Monitor a subreddit for a phrase and print alerts (optionally send Discord)."""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen

import praw
from prawcore.exceptions import PrawcoreException
from dotenv import load_dotenv

load_dotenv()
def alert(item, phrase, webhook_url):
	author = str(item.author) if item.author else "[deleted]"
	message = (
		f"Found {phrase!r} in r/{item.subreddit.display_name} by u/{author}: "
		f"{item.permalink}"
	)
	print(f"\a{message}", flush=True)

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
		except Exception as error:
			print(f"Discord notification failed: {error}", flush=True)


def watch(stream, phrase, webhook_url):
	while True:
		try:
			for item in stream:
				text = "\n".join(
					getattr(item, field, "") or ""
					for field in ("title", "selftext", "body")
				)
				if phrase.casefold() in text.casefold():
					alert(item, phrase, webhook_url)
		except PrawcoreException as error:
			print(f"Reddit connection error: {error}; retrying in 10 seconds", flush=True)
			time.sleep(10)


def main():
	subreddit_name = os.getenv("SUBREDDIT", "").strip()
	phrase = os.getenv("MATCH_PHRASE", "").strip()
	client_id = os.getenv("REDDIT_CLIENT_ID", "").strip()
	client_secret = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
	user_agent = os.getenv("REDDIT_USER_AGENT", "keyword-monitor/1.0").strip()
	webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
	print("subreddit_name", subreddit_name)
	if not all((subreddit_name, phrase, client_id, client_secret)):
		raise SystemExit(
			"Set SUBREDDIT, MATCH_PHRASE, REDDIT_CLIENT_ID, and "
			"REDDIT_CLIENT_SECRET environment variables."
		)
	
	# reddit = praw.Reddit(
	# 	client_id=client_id,
	# 	client_secret=client_secret,
	# 	user_agent=user_agent,
	# )
	# subreddit = reddit.subreddit(subreddit_name)
	# print(f"Watching r/{subreddit_name} for {phrase!r} in new posts and comments.")

	# with ThreadPoolExecutor(max_workers=2) as executor:
	# 	streams = (
	# 		subreddit.stream.submissions(skip_existing=True),
	# 		subreddit.stream.comments(skip_existing=True),
	# 	)
	# 	for stream in streams:
	# 		executor.submit(watch, stream, phrase, webhook_url)


if __name__ == "__main__":
	main()

