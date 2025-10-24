from infrastructure.scraper.registry import AdapterRegistry
from infrastructure.scraper.adapters import example_news
from infrastructure.scraper.adapters import coindesk
from infrastructure.db.schema import upsert_article

registry = AdapterRegistry(adapters=[
    example_news.ExampleNewsAdapter(),
    coindesk.CoindeskAdapter()
])

def run():
    for article in registry.crawl_all():
        if(article.type == 'website'):
            id = upsert_article(article)
        else:
            #  @todo: insert for a price.
            x=1
