"""Writes sample RSS feeds for testing the engine without the internet.

Every company, person and event here is MADE UP. These files exist only to
check that clustering, scoring, exclusions and the email work as designed.

  python tests/make_fixtures.py --now 2026-10-22T05:31Z --out tests/fixtures
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

# (feed id, minutes ago, title, summary, url, google <source> (name, url) or None)
STORIES = [
    # 1. Big funding round, many outlets incl. a Google News Reuters item -> should be the top story
    ("uktn", 300, "Leeds AI startup Quillon raises £40m Series B to expand into US",
     "Quillon, which builds AI agents for insurers, has raised £40m in a Series B round led by Northgate Ventures.",
     "https://www.uktech.news/ai/quillon-series-b", None),
    ("sifted", 280, "Quillon bags £40m as UK insurtech AI race heats up",
     "The Leeds-based startup says the Series B will fund 120 new hires and a New York office.",
     "https://sifted.eu/articles/quillon-40m", None),
    ("cityam", 260, "Leeds AI firm Quillon lands £40m Series B funding round",
     "Quillon raised £40m from Northgate Ventures and existing investors to grow its AI agents for insurers.",
     "https://www.cityam.com/quillon-40m-series-b/", None),
    ("techcrunch", 240, "Quillon, a UK startup building AI agents for insurers, raises $52M Series B",
     "UK-based Quillon has raised a $52 million Series B round to expand into the US insurance market.",
     "https://techcrunch.com/2026/10/22/quillon-series-b/", None),
    ("gn-funding", 230, "UK's Quillon raises 40 million pounds in Series B round - Reuters",
     "Quillon Series B Reuters", "https://news.google.com/rss/articles/QUILLON1",
     ("Reuters", "https://www.reuters.com")),
    ("prolific-north", 200, "Leeds AI scaleup Quillon secures £40m Series B",
     "Quillon, founded in Leeds in 2021, has secured £40m Series B investment led by Northgate Ventures.",
     "https://www.prolificnorth.co.uk/news/quillon-series-b", None),
    ("ft-tech", 190, "Quillon raises £40mn as investors pile into AI for insurance",
     "The Leeds-based company's Series B values it at about £300mn, people familiar said.",
     "https://www.ft.com/content/quillon-40mn", None),

    # 2. Regulator action + outlets -> policy, regulator bonus
    ("cma", 400, "CMA opens investigation into Halcyon Cloud's acquisition of DataNest",
     "The Competition and Markets Authority has opened a phase 1 investigation into the proposed deal.",
     "https://www.gov.uk/government/news/cma-halcyon-datanest", None),
    ("bbc-tech", 380, "Competition watchdog to investigate Halcyon Cloud's DataNest takeover",
     "The CMA said the deal could reduce competition in UK cloud storage for public sector bodies.",
     "https://www.bbc.co.uk/news/articles/halcyon-cma", None),
    ("guardian-tech", 370, "CMA launches investigation into Halcyon Cloud DataNest acquisition",
     "The competition watchdog will examine whether the takeover harms UK public sector cloud buyers.",
     "https://www.theguardian.com/technology/2026/oct/22/cma-halcyon-datanest", None),
    ("computer-weekly", 330, "CMA investigation into Halcyon Cloud DataNest deal puts public sector cloud in focus",
     "Public sector IT buyers say the takeover would leave few alternatives for government cloud storage.",
     "https://www.computerweekly.com/news/halcyon-datanest-cma", None),

    # 3. Ransomware at an NHS trust -> security
    ("bbc-tech", 600, "Ransomware attack takes Northshire NHS trust patient systems offline",
     "Hospitals in Northshire have cancelled appointments after a ransomware attack hit patient record systems.",
     "https://www.bbc.co.uk/news/articles/northshire-ransomware", None),
    ("sky-tech", 590, "Northshire NHS hospitals hit by ransomware attack, appointments cancelled",
     "The trust said patient record systems were taken offline as a precaution after the cyber attack.",
     "https://news.sky.com/story/northshire-nhs-ransomware", None),
    ("the-register", 560, "Northshire NHS trust ransomware attack: patient systems still offline",
     "Ransomware crew claims responsibility for the Northshire NHS cyber attack, threatens to leak data.",
     "https://www.theregister.com/2026/10/22/northshire_nhs_ransomware/", None),
    ("ncsc", 500, "NCSC statement on ransomware attack affecting Northshire NHS trust",
     "The NCSC is working with Northshire NHS trust to understand the impact of the ransomware attack.",
     "https://www.ncsc.gov.uk/news/statement-northshire-nhs", None),
    ("infosecurity", 480, "Northshire NHS trust ransomware attack disrupts hospital patient systems",
     "The attack on the NHS trust is the third ransomware incident to hit UK hospitals this year.",
     "https://www.infosecurity-magazine.com/news/northshire-nhs-ransomware/", None),
    ("bleepingcomputer", 470, "Ransomware gang claims attack on UK's Northshire NHS trust",
     "A ransomware group has claimed the attack on the Northshire NHS trust in the UK.",
     "https://www.bleepingcomputer.com/news/security/northshire-nhs-ransomware/", None),

    # 4. Global AI launch, no UK angle, only global outlets -> held back (no UK angle)
    ("techcrunch", 150, "Vantage Labs releases Atlas 5, its most capable AI model yet",
     "The San Francisco company says Atlas 5 beats rivals on coding and maths benchmarks.",
     "https://techcrunch.com/2026/10/22/vantage-atlas-5/", None),
    ("the-verge", 140, "Vantage Labs Atlas 5 AI model is here",
     "Atlas 5 is available today for paying subscribers in the US.", "https://www.theverge.com/vantage-atlas-5", None),
    ("ars-technica", 130, "Vantage Labs launches Atlas 5 AI model with longer memory",
     "The new model can remember far more of a conversation than Atlas 4.",
     "https://arstechnica.com/ai/2026/10/vantage-atlas-5/", None),

    # 5. Big tech in the UK
    ("bbc-tech", 700, "Apple to open London engineering hub with 500 new jobs",
     "Apple says the King's Cross site will focus on chip design and AI research.",
     "https://www.bbc.co.uk/news/articles/apple-london-hub", None),
    ("guardian-tech", 690, "Apple to create 500 jobs at new London engineering centre",
     "The engineering hub in King's Cross will open next year, Apple said.",
     "https://www.theguardian.com/technology/2026/oct/22/apple-london-jobs", None),
    ("cityam", 650, "Apple London engineering hub to bring 500 jobs to King's Cross",
     "Apple's new London engineering hub is a boost for the capital's tech sector.",
     "https://www.cityam.com/apple-london-hub/", None),
    ("the-verge", 640, "Apple is opening a big new engineering hub in London",
     "Apple's London engineering office will employ 500 people working on chips and AI in the UK.",
     "https://www.theverge.com/apple-london-hub", None),

    # 6. Should be excluded by rules
    ("itpro", 100, "Pixel 11 review: a solid phone held back by its battery",
     "We spent two weeks with the Pixel 11.", "https://www.itpro.com/hardware/pixel-11-review", None),
    ("cityam", 90, "Black Friday deals: the best tech discounts so far",
     "Early Black Friday deals on laptops and phones.", "https://www.cityam.com/black-friday-deals/", None),
    ("silicon-uk", 80, "The best laptops for business in 2026",
     "Our pick of business laptops.", "https://www.silicon.co.uk/best-laptops-2026", None),

    # 7. Primary source with no outlet coverage -> held back until an outlet confirms
    ("deepmind", 60, "Introducing GenoFold 2",
     "GenoFold 2 predicts how proteins interact with DNA.", "https://deepmind.google/blog/genofold-2/", None),

    # 8. Single-outlet paywalled exclusive
    ("telegraph-tech", 240, "Exclusive: Treasury drawing up AI tax break for small businesses",
     "The Treasury is preparing a tax break for small firms that adopt AI tools, sources said.",
     "https://www.telegraph.co.uk/business/2026/10/22/treasury-ai-tax-break/", None),

    # 9. Small funding round, one outlet
    ("uktn", 520, "Bristol robotics startup Ferrous raises £6m seed round",
     "Ferrous builds robots for warehouse picking and will use the seed funding to hire 20 engineers.",
     "https://www.uktech.news/robotics/ferrous-seed", None),

    # 10. Online Safety Act (Ofcom via Google News)
    ("ofcom", 900, "Ofcom names first platforms that must bring in age checks under Online Safety Act - Ofcom",
     "Ofcom names platforms", "https://news.google.com/rss/articles/OFCOM1", ("Ofcom", "https://www.ofcom.org.uk")),
    ("guardian-tech", 880, "Ofcom names first platforms required to introduce age checks",
     "The regulator said six platforms must have age checks in place under the Online Safety Act by March.",
     "https://www.theguardian.com/technology/2026/oct/22/ofcom-age-checks", None),
    ("sky-tech", 860, "Ofcom names six platforms that must introduce age checks under Online Safety Act",
     "Platforms that fail to bring in age checks could face fines of up to 10% of global turnover.",
     "https://news.sky.com/story/ofcom-age-checks", None),
    ("wired-uk", 800, "The UK's Online Safety Act age checks are about to get real",
     "Ofcom has named the first platforms that must verify users' ages in the UK.",
     "https://www.wired.com/story/uk-online-safety-act-age-checks/", None),

    # 11. AISI report
    ("aisi", 1000, "AI Security Institute publishes results of frontier model testing",
     "The AI Security Institute has published results from testing five frontier AI models.",
     "https://www.gov.uk/government/news/aisi-frontier-testing", None),
    ("computer-weekly", 950, "AI Security Institute frontier model testing finds gaps in safeguards",
     "The UK AI Security Institute found that safeguards on frontier AI models could be bypassed.",
     "https://www.computerweekly.com/news/aisi-frontier-testing", None),
    ("itpro", 940, "UK AI Security Institute frontier model testing: what the results show",
     "The AI Security Institute tested five frontier models for cyber and bio risks.",
     "https://www.itpro.com/ai/aisi-frontier-testing", None),

    # 12. Phishing at a council, includes an outlet we don't track (via Google News)
    ("the-register", 420, "Edinburgh council staff targeted by phishing scam after email breach",
     "City of Edinburgh Council says a phishing scam reached staff after an email account was breached.",
     "https://www.theregister.com/2026/10/22/edinburgh_council_phishing/", None),
    ("gn-security", 410, "Edinburgh council warns staff over phishing scam - Edinburgh Evening News",
     "Edinburgh phishing", "https://news.google.com/rss/articles/EDI1",
     ("Edinburgh Evening News", "https://www.edinburghnews.scotsman.com")),

    # 13. UK prices
    ("the-verge", 340, "Microsoft is raising Xbox Game Pass prices in the UK",
     "Game Pass Ultimate will cost £15.99 a month in the UK from December.",
     "https://www.theverge.com/xbox-game-pass-uk-price", None),
    ("the-register", 320, "Microsoft hikes UK Xbox Game Pass prices to £15.99",
     "Xbox Game Pass Ultimate subscribers in the UK will pay £15.99 a month from December.",
     "https://www.theregister.com/2026/10/22/xbox_game_pass_uk/", None),
    ("silicon-uk", 300, "Microsoft raises Xbox Game Pass UK prices",
     "Microsoft is raising Game Pass prices in the UK to £15.99 a month.",
     "https://www.silicon.co.uk/xbox-game-pass-uk-prices", None),

    # 14. Acquisition
    ("sifted", 640, "Manchester fintech Ledgerly acquired by US rival Tallyworks for £120m",
     "Ledgerly, the Manchester accounting software startup, has been acquired by Tallyworks.",
     "https://sifted.eu/articles/ledgerly-tallyworks", None),
    ("uktn", 620, "Tallyworks buys Manchester fintech Ledgerly in £120m acquisition",
     "Manchester-based Ledgerly has been acquired by US rival Tallyworks in a £120m deal.",
     "https://www.uktech.news/fintech/ledgerly-acquired", None),

    # 15. AI in the NHS
    ("bbc-tech", 760, "NHS to trial AI triage tool across 20 hospital trusts",
     "Patients in 20 NHS trusts will have A&E symptoms checked by an AI triage tool from January.",
     "https://www.bbc.co.uk/news/articles/nhs-ai-triage", None),
    ("guardian-tech", 740, "NHS AI triage trial to cover 20 hospital trusts",
     "Doctors' groups have raised concerns about the NHS AI triage trial.",
     "https://www.theguardian.com/society/2026/oct/22/nhs-ai-triage", None),
    ("computing", 720, "NHS AI triage tool trial expands to 20 trusts",
     "The AI triage tool will be trialled across 20 NHS trusts from January.",
     "https://www.computing.co.uk/news/nhs-ai-triage", None),

    # 16. London AI startup launch
    ("uktn", 210, "London AI startup Sable launches voice agent for GP surgeries",
     "Sable's AI voice agent answers GP surgery phone lines and books appointments.",
     "https://www.uktech.news/ai/sable-gp-voice-agent", None),
    ("tech-eu", 190, "London's Sable launches AI voice agent to cut GP phone queues",
     "Sable, a London AI startup, has launched an AI voice agent for GP practices in the UK.",
     "https://tech.eu/2026/10/22/sable-ai-gp/", None),
]

RSS_HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel><title>{0}</title><link>https://example.com</link><description>Test feed</description>\n'


def write(now: datetime, out: Path) -> int:
    out.mkdir(parents=True, exist_ok=True)
    feeds = defaultdict(list)
    for feed, mins, title, summary, url, gsrc in STORIES:
        when = format_datetime(now - timedelta(minutes=mins))
        src = f'<source url="{escape(gsrc[1])}">{escape(gsrc[0])}</source>' if gsrc else ""
        feeds[feed].append(
            f"<item><title>{escape(title)}</title><link>{escape(url)}</link><guid>{escape(url)}</guid>"
            f"<pubDate>{when}</pubDate><description>{escape(summary)}</description>{src}</item>"
        )
    for feed, items in feeds.items():
        (out / f"{feed}.xml").write_text(RSS_HEAD.format(feed) + "\n".join(items) + "\n</channel></rss>\n",
                                         encoding="utf-8")
    return len(feeds)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", required=True)
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "fixtures")
    a = ap.parse_args()
    now = datetime.strptime(a.now, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
    print(f"wrote {write(now, a.out)} feeds")
