import re

def clean_url(url: str) -> str:
    """
    Remove tracking params so we have one canonical URL per item.
    Example:
      https://www.ebay.co.uk/itm/357801575030?mkcid=1&...
      → https://www.ebay.co.uk/itm/357801575030
    """
    return url.split("?")[0].strip()


def extract_item_id(url: str) -> str:
    """
    Extract the eBay item ID from a listing URL.
    Example:
      https://www.ebay.co.uk/itm/357801575030 → 357801575030
    """
    match = re.search(r'/itm/(\d+)', url)
    return match.group(1) if match else url
