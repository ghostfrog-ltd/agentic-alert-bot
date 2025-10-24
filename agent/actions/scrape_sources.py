from infrastructure.scraper.registry import AdapterRegistry
from infrastructure.scraper import example_news
from infrastructure.db.schema import upsert_article

registry = AdapterRegistry(adapters=[
    example_news.ExampleNewsAdapter(),
])

def run():
    for article in registry.crawl_all():
        if(article.type == 'website'):
            id = upsert_article(article)
            print(f"ID: {id}")
        else:
            # is a price
            x=1





