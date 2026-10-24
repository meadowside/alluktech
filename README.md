# All UK Tech

All of UK tech, ranked by who's covering it.

A small Python engine that runs every hour on GitHub Actions. It reads about 40 news feeds,
groups articles about the same event into stories, ranks them by how many outlets cover them,
and publishes a static site. On weekday mornings it also builds the email for Substack.
No AI is needed to run it. An optional cheap model writes six "why it matters" lines a day.

## How it works

```
feeds (config/sources.yaml)
  -> fetch + rules (exclusions, UK relevance, section)      engine/fetch.py, engine/rules.py
  -> group into stories, score                              engine/stories.py
  -> site: index, 5 section pages, status page              engine/publish.py, templates/
  -> 6am UK on weekdays: email page to paste into Substack
  -> 8am, 12pm, 6pm UK: top AI story to Facebook            engine/facebook.py
```

Score = min(coverage, 15) x UK relevance x freshness (halves every 12 hours) + 2 if a regulator
is in the story. Coverage adds each outlet once, at its weight (national 3, UK specialist 2,
global and primary 1). Everything you might want to change is in `config/`.

## Setup (about 30 minutes)

1. **Create a GitHub repo** called `alluktech` and push this folder to it.
   Make it public: GitHub Pages and Actions minutes are free for public repos.
   (Private works too, but Pages then needs a paid GitHub plan.)
2. **Turn on Pages**: repo Settings > Pages > Source: **GitHub Actions**.
3. **Run it once**: Actions tab > Hourly run > Run workflow. After it finishes the site is live
   at `https://<your-username>.github.io/alluktech/`.
4. **Check the feeds**: open `/status.html` on the site. Any source marked Failing needs a new
   URL in `config/sources.yaml`. Expect a few on day one; feed addresses change.
5. **Point the domain**: in Settings > Pages, set the custom domain to `alluktech.com`. At your
   registrar add four A records for `@` pointing to 185.199.108.153, 185.199.109.153,
   185.199.110.153 and 185.199.111.153, and a CNAME for `www` pointing to
   `<your-username>.github.io`. Tick "Enforce HTTPS" once it's offered.
   Point alluktech.co.uk and alluktech.uk at alluktech.com with your registrar's redirect option.

From then on it runs by itself every hour at :31.

## Optional extras (repo Settings > Secrets and variables > Actions)

| Secret | What it turns on |
| --- | --- |
| `ANTHROPIC_API_KEY` | AI-written "So what" lines under the stories on the home page (top story plus the top 6 in each section) and in the email. Each story is written once and saved, so repeat builds cost nothing. Capped at 25 new lines an hour and 150 a day (`ai_lines` in `config/rules.yaml`). Without a key the site simply shows no line. Set `LLM_MODEL` to change model. |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | A message to your phone when the email is ready. |
| `FB_PAGE_ID`, `FB_PAGE_TOKEN` | Real Facebook posts at 8am, 12pm and 6pm: the top new AI story's headline, its "So what" line and the outlet count, linking to the story on alluktech.com. Without them, `data/last_run.log` shows the exact post it *would* have made, so you can check its picks first. The token is a long-lived Page access token with `pages_manage_posts`, from a Meta developer app you own. |

## Your morning

1. At about 6:35 open `alluktech.com/email/latest.html` (or tap the Telegram link).
2. Click **Copy email**, paste into a new Substack post.
3. Replace the yellow bits: the opening note and the top story's "why it matters".
4. Send.

## Coverage history (trends)

Every run also notes which companies each story is about (names and spellings in
`config/companies.yaml`, which also covers a few regulators and public bodies) and rolls the
coverage into two tables that are never pruned: `company_daily` (one row per company per UK day:
stories, headline stories, outlets, attention) and `section_daily` (articles per section per day).
Recent days are rebuilt each run while stories are still growing, then freeze. Attention is the
site's own weighted coverage (national 3, UK specialist 2, others 1), full weight when the company
is in the headline and half when it's only in the text. Code in `engine/trends.py`.

Nothing on the site or in the email uses it yet. To see what it says:

```
python run.py trends                 # yesterday
python run.py trends --date 2026-11-12
```

Spikes stay blank until there are 14 days of history and "new on the radar" until there are 60.
Treat the first fortnight as warm-up. It all describes how much the press covered something, never
whether the news was good or bad.

## Tuning after the first week

All in `config/rules.yaml`:

- **Same story listed twice?** Lower `min_link_similarity` and `merge_similarity` a little.
- **Two different stories merged?** Raise them.
- **Too much global news?** Set `uk_outlet_floor` to 1 so BBC and others must show a UK angle too.
- **Wrong section?** Add words to `section_keywords`.
- **Junk getting through?** Add phrases to `exclude.title_keywords` or URL bits to `exclude.url_patterns`.

The status page lists the top 40 stories with their scores and why any were held back.

## Running it on your own machine

```
pip install -r requirements.txt
python run.py hourly                 # fetch and build the site into ./site
python run.py email                  # build today's email now
```

Test without the internet, using made-up sample stories:

```
python tests/make_fixtures.py --now 2026-10-22T05:31Z
python run.py auto --fixtures tests/fixtures --now 2026-10-22T05:31Z --db data/test/news.db --out site-test
```
