import json
import re
from urllib.parse import quote_plus

import requests

from common_crawler.utils import URLWithParameters, UrlResults
from common_crawler.cache import CommonCrawlerCacheManager, CacheStorage

"""
This module contains classes for managing a cache of Common Crawl search results
"""


class CommonCrawlerManager:
    """
    This class orchestrates the crawling process, leveraging CommonCrawler for
    actual interactions with the Common Crawl Index Server and CommonCrawlerCacheManager
    for caching results.
    It validates crawl ids, manages pagination, and aggregates results.
    """

    def __init__(self, cache_storage: CacheStorage):
        self.cache = CommonCrawlerCacheManager(
            storage=cache_storage
        )

    def reset_cache(self):
        self.cache.cache = {}
        print("Cache has been reset.")

    def crawl(self, crawl_id, search_term, keyword, num_pages) -> list[UrlResults]:

        # Check that crawl_id is valid
        if not re.match(r'CC-MAIN-\d{4}-\d{2}', crawl_id):
            raise ValueError("Invalid crawl_id")

        print(f"Searching for {keyword} on {search_term} in {crawl_id} for {num_pages} pages")

        cc = CommonCrawler(crawl_id)
        results: list[UrlResults] = []

        # Retrieve the cache object
        cache_object = self.cache.get(crawl_id, search_term, keyword)
        start_page = cache_object.last_page + 1
        end_page = start_page + num_pages

        for next_page in range(start_page, end_page):
            records = cc.search_common_crawl_index(search_term, next_page)

            # If records were found, filter them and add to results
            if not records:
                continue

            keyword_urls = cc.get_urls_with_keyword(records, keyword)
            for keyword_url in keyword_urls:
                results.append(
                    UrlResults(
                        index=crawl_id,
                        url=keyword_url,
                        search_term=search_term,
                        page=next_page,
                        keyword=keyword))

            cache_object.last_page = next_page

        # cache_object is updated in-place; calling save_cache persists these changes.
        self.cache.save_cache()

        return results


class CommonCrawler:
    """
    This class is used to interact directly with the Common Crawl Index Server,
    encapsulating the logic for making HTTP requests, handling pagination, and extracting data from the responses.
    """

    def __init__(self, crawl_id='CC-MAIN-2023-50'):
        self.CC_INDEX_SERVER = 'http://index.commoncrawl.org/'
        self.INDEX_NAME = f'{crawl_id}-index'
        self.root_url = f'{self.CC_INDEX_SERVER}{self.INDEX_NAME}'

    def get_number_of_pages(self, url):
        """
        This method is used to get the number of pages in the Common Crawl index for a given URL
         along with an estimate of the total number of records (assuming 15,000 records per page)
        Args:
            url: URL to query off of
        """
        encoded_url = quote_plus(url)
        search_url = URLWithParameters(self.root_url)
        search_url.add_parameter('url', encoded_url)
        search_url.add_parameter('output', 'json')
        search_url.add_parameter('showNumPages', 'true')
        response = requests.get(str(search_url))
        if response.status_code == 200:
            json_response = json.loads(response.text.strip())
            print(f"Found {json_response['pages']} pages for {url}")
            # Estimate also the total number of records
            total_records = json_response['pages'] * 15000
            print(f"Estimated {total_records} records for {url}")
        else:
            print(f"Failed to get number of pages for {url}")

    def search_common_crawl_index(self, url: str, page: int = 0) -> list[dict]:
        """
        This method is used to search the Common Crawl index for a given URL and page number
        Args:
            url: a URL to search for
            page: the page number to search

        Returns: A list of records (dictionaries) containing the search results

        """
        encoded_url = quote_plus(url)
        search_url = URLWithParameters(self.root_url)
        search_url.add_parameter('url', encoded_url)
        search_url.add_parameter('output', 'json')
        search_url.add_parameter('page', page)

        # Perform an HTTP GET request to retrieve records for the encoded URL.
        response = requests.get(str(search_url))

        # If the request is successful, parse each record from the response and return them.
        if response.status_code == 200:
            records = response.text.strip().split('\n')
            print(f"Found {len(records)} records for {url} on page {page}")
            return [json.loads(record) for record in records]
        else:
            # Return None to indicate that no records were found or an error occurred.
            return None

    @staticmethod
    def get_urls_with_keyword(records: list[dict], keyword) -> list[str]:
        return [record['url'] for record in records if keyword in record['url']]
