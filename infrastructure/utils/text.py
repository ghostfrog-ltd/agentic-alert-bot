from bs4 import BeautifulSoup


def extract_content(html: str) -> str:
    """
    Try several common containers, fall back to all <p> inside <article>.
    Returns a single newline-joined string.
    """
    soup = BeautifulSoup(html, "lxml")

    # Likely containers used across publishers (including CoinDesk variants over time)
    candidates = [
        '[data-testid="ArticleBody"]',
        '[data-component="ArticleBody"]',
        'section[name="articleBody"]',
        'div[itemprop="articleBody"]',
        'div[class*="article-body"]',
        'article',
        'main article',
    ]

    for sel in candidates:
        node = soup.select_one(sel)
        if node:
            ps = [p.get_text(" ", strip=True) for p in node.select("p") if p.get_text(strip=True)]
            if ps:
                return "\n\n".join(ps)

    # Fallback: all <p> under <article>, or all <p> in page
    art = soup.select_one("article")
    if art:
        ps = [p.get_text(" ", strip=True) for p in art.select("p") if p.get_text(strip=True)]
        if ps:
            return "\n\n".join(ps)

    ps = [p.get_text(" ", strip=True) for p in soup.select("p") if p.get_text(strip=True)]
    return "\n\n".join(ps[:20])  # cap to avoid grabbing entire nav/footer text


def classify_sentiment(text: str) -> tuple[str, float]:
    """
    Uses VADER if installed; otherwise returns ('neutral', 0.0).
    pip install vaderSentiment
    """
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        analyzer = SentimentIntensityAnalyzer()
        score = analyzer.polarity_scores(text or "")
        comp = score.get("compound", 0.0)
        if comp >= 0.05:
            return "positive", comp
        elif comp <= -0.05:
            return "negative", comp
        else:
            return "neutral", comp
    except Exception:
        return "neutral", 0.0


# Common junk patterns often found in footer-only or boilerplate pages
_FOOTER_MARKERS = {
    "subscribe", "newsletter", "cookies", "privacy policy", "related stories",
    "most read", "trending", "advertisement", "sponsored", "back to top",
    "terms of service", "©",
    # 👇 CoinDesk-specific junk we saw
    "coindesk is an award-winning media outlet",
    "bullish (nyse:blsh)",
    "editorial policies",
}


def is_footer_only(html: str) -> bool:
    """
    Returns True if the scraped HTML looks like junk (footer/boilerplate)
    or is too short to be a real article.
    """
    if not html:
        return True

    soup = BeautifulSoup(html, "lxml")

    # Strip obvious non-content sections
    for sel in [
        "footer", "nav", "aside", "script", "style",
        "[role='navigation']", ".newsletter", ".subscription",
        ".paywall", ".promo", ".advert", ".ads"
    ]:
        for el in soup.select(sel):
            el.decompose()

    text = soup.get_text(" ", strip=True).lower()

    # Too little content? Probably garbage.
    if len(text) < 300:
        return True

    # Check for boilerplate language
    matches = sum(1 for word in _FOOTER_MARKERS if word in text)
    return matches >= 3
