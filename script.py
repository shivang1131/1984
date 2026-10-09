
"""Monitor Reddit posts for negative mentions and email alerts."""

import json
import os
import smtplib
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

import praw
from dotenv import load_dotenv
from prawcore.exceptions import PrawcoreException
from transformers import pipeline


load_dotenv()

MODEL_NAME = "cardiffnlp/twitter-roberta-base-sentiment-latest"
STATE_FILE = Path(os.getenv("STATE_FILE", "seen_posts.json"))
LOOKBACK_MINUTES = int(os.getenv("LOOKBACK_MINUTES", "15"))
POST_LIMIT = int(os.getenv("POST_LIMIT", "1000"))
STATE_RETENTION_DAYS = 7


def get_config():
    """Load and validate configuration."""
    subreddits = [
        s.strip().removeprefix("r/")
        for s in os.getenv(
            "SUBREDDITS", os.getenv("SUBREDDIT", "")
        ).split(",")
        if s.strip()
    ]

    phrases = [
        p.strip()
        for p in os.getenv(
            "MATCH_PHRASES", os.getenv("MATCH_PHRASE", "")
        ).split(",")
        if p.strip()
    ]

    config = {
        "subreddits": subreddits,
        "phrases": phrases,
        "client_id": os.getenv("REDDIT_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("REDDIT_CLIENT_SECRET", "").strip(),
        "user_agent": os.getenv(
            "REDDIT_USER_AGENT", "reddit-negative-monitor/1.0"
        ).strip(),
        "gmail_address": os.getenv("GMAIL_ADDRESS", "").strip(),
        "gmail_app_password": os.getenv(
            "GMAIL_APP_PASSWORD", ""
        ).replace(" ", "").strip(),
        "alert_email_to": os.getenv("ALERT_EMAIL_TO", "").strip(),
        "negative_threshold": float(
            os.getenv("NEGATIVE_THRESHOLD", "0.70")
        ),
    }

    required = [
        "subreddits",
        "phrases",
        "client_id",
        "client_secret",
        "gmail_address",
        "gmail_app_password",
        "alert_email_to",
    ]

    missing = [key for key in required if not config[key]]
    if missing:
        raise SystemExit(
            "Missing configuration: " + ", ".join(missing)
        )

    if not 0 <= config["negative_threshold"] <= 1:
        raise SystemExit("NEGATIVE_THRESHOLD must be between 0 and 1.")

    if LOOKBACK_MINUTES < 1 or POST_LIMIT < 1:
        raise SystemExit("LOOKBACK_MINUTES and POST_LIMIT must be positive.")

    return config


def load_state():
    """Load processed post IDs from a JSON file."""
    if not STATE_FILE.exists():
        return {}

    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        seen = data.get("seen", {})
        if not isinstance(seen, dict):
            return {}
        return {
            post_id: float(timestamp)
            for post_id, timestamp in seen.items()
        }
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise RuntimeError(
            f"Could not read {STATE_FILE}: {error}"
        ) from error


def save_state(seen):
    """Save processed post IDs and prune old entries."""
    cutoff = time.time() - STATE_RETENTION_DAYS * 24 * 60 * 60
    seen = {
        post_id: timestamp
        for post_id, timestamp in seen.items()
        if timestamp >= cutoff
    }

    STATE_FILE.write_text(
        json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "seen": seen,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def send_email(config, post, matched_phrases, sentiment, confidence):
    """Send a Gmail alert for a negative post."""
    message = EmailMessage()
    message["From"] = config["gmail_address"]
    message["To"] = config["alert_email_to"]
    message["Subject"] = (
        f"[Reddit Alert] Negative mention in r/{post.subreddit.display_name}"
    )

    body = (
        "A potentially negative Reddit mention was detected.\n\n"
        f"Subreddit: r/{post.subreddit.display_name}\n"
        f"Matched phrase(s): {', '.join(matched_phrases)}\n"
        f"Post title: {post.title}\n"
        f"Author: u/{post.author if post.author else '[deleted]'}\n"
        f"Sentiment: {sentiment}\n"
        f"Model confidence: {confidence:.1%}\n"
        f"Posted at: {datetime.fromtimestamp(post.created_utc, timezone.utc).isoformat()}\n"
        f"Link: https://www.reddit.com{post.permalink}\n\n"
        "Post text:\n"
        f"{(post.selftext or '[No post body]')[:5000]}\n\n"
        "Note: automated sentiment classification can be wrong."
    )
    message.set_content(body)

    # Gmail SMTP over SSL.
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as smtp:
        smtp.login(
            config["gmail_address"],
            config["gmail_app_password"],
        )
        smtp.send_message(message)

    print(f"Email alert sent for Reddit post {post.id}", flush=True)


def main():
    config = get_config()
    seen = load_state()

    print(f"Monitoring: {', '.join('r/' + s for s in config['subreddits'])}")
    print(f"Target phrases: {', '.join(config['phrases'])}")
    print(f"Negative confidence threshold: {config['negative_threshold']:.0%}")

    reddit = praw.Reddit(
        client_id=config["client_id"],
        client_secret=config["client_secret"],
        user_agent=config["user_agent"],
    )

    # Load the open-source model once per run.
    classifier = pipeline(
        "text-classification",
        model=MODEL_NAME,
        device=-1,  # CPU
    )

    cutoff = time.time() - LOOKBACK_MINUTES * 60
    checked = 0
    classified = 0
    alerts = 0
    state_changed = False

    try:
        for subreddit_name in config["subreddits"]:
            subreddit = reddit.subreddit(subreddit_name)
            print(f"\nScanning r/{subreddit_name}...", flush=True)

            for post in subreddit.new(limit=POST_LIMIT):
                # Posts are returned newest first.
                if post.created_utc < cutoff:
                    break

                checked += 1

                # A previously processed matching post is ignored.
                if post.id in seen:
                    continue

                text = f"{post.title}\n\n{post.selftext or ''}"
                folded_text = text.casefold()

                matched_phrases = [
                    phrase
                    for phrase in config["phrases"]
                    if phrase.casefold() in folded_text
                ]

                # Don't run the model on posts without a target phrase.
                if not matched_phrases:
                    continue

                # Keep input manageable. The model tokenizer also truncates
                # inputs to its supported token length.
                analysis_text = text[:4000]

                result = classifier(
                    analysis_text,
                    truncation=True,
                    max_length=512,
                )[0]

                sentiment = result["label"].strip().lower()
                confidence = float(result["score"])
                classified += 1

                print(
                    f"{post.id}: {matched_phrases} -> "
                    f"{sentiment} ({confidence:.1%})",
                    flush=True,
                )

                # Record this matching post after successful classification.
                seen[post.id] = float(post.created_utc)
                state_changed = True

                if (
                    sentiment == "negative"
                    and confidence >= config["negative_threshold"]
                ):
                    # If email sending fails, the run fails rather than
                    # silently treating this alert as successfully delivered.
                    send_email(
                        config,
                        post,
                        matched_phrases,
                        sentiment,
                        confidence,
                    )
                    alerts += 1

    except PrawcoreException as error:
        raise RuntimeError(f"Reddit API error: {error}") from error

    if state_changed:
        save_state(seen)

    print(
        f"\nDone. Checked {checked} posts, classified {classified} "
        f"matching posts, sent {alerts} email alert(s).",
        flush=True,
    )


if __name__ == "__main__":
    main()